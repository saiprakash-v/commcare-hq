from __future__ import absolute_import, unicode_literals

import os
import uuid
import zipfile
from datetime import datetime, timedelta
from io import open

from django.conf import settings

import six
from celery.schedules import crontab
from celery.task import periodic_task, task
from celery.utils.log import get_task_logger
from dateutil.parser import parse
from six.moves import filter, map
from unidecode import unidecode

from casexml.apps.case.xform import extract_case_blocks
from couchforms.analytics import app_has_been_submitted_to_in_last_30_days
from dimagi.utils.logging import notify_exception
from soil import DownloadBase
from soil.util import expose_blob_download

from corehq.apps.domain.calculations import all_domain_stats, calced_props
from corehq.apps.domain.models import Domain
from corehq.apps.es import filters
from corehq.apps.es.domains import DomainES
from corehq.apps.es.forms import FormES
from corehq.apps.hqwebapp.tasks import send_mail_async
from corehq.apps.reports.util import send_report_download_email
from corehq.blobs import CODES, get_blob_db
from corehq.const import ONE_DAY
from corehq.elastic import (
    ES_META,
    get_es_new,
    send_to_elasticsearch,
    stream_es_query,
)
from corehq.form_processor.interfaces.dbaccessors import FormAccessors
from corehq.pillows.mappings.app_mapping import APP_INDEX
from corehq.util.files import TransientTempfile, safe_filename_header
from corehq.util.view_utils import absolute_reverse

from .analytics.esaccessors import (
    get_form_ids_having_multimedia,
    scroll_case_names,
)

logging = get_task_logger(__name__)
EXPIRE_TIME = ONE_DAY
MAX_MM_ZIP_GBS = 5


@periodic_task(run_every=crontab(hour="22", minute="0", day_of_week="*"), queue='background_queue')
def update_calculated_properties():
    success = False
    try:
        _update_calculated_properties()
        success = True
    except Exception:
        notify_exception(
            None,
            message="update_calculated_properties task has errored",
        )
    send_mail_async.delay(
        subject="Calculated properties report task was " + ("successful" if success else "unsuccessful"),
        message="Sentry will have relevant exception in case of failure",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=["{}@{}.com".format("dmore", "dimagi")]
    )


def _update_calculated_properties():
    results = DomainES().filter(
        get_domains_to_update_es_filter()
    ).fields(["name", "_id"]).run().hits

    all_stats = all_domain_stats()
    for r in results:
        dom = r["name"]
        domain_obj = Domain.get_by_name(dom)
        if not domain_obj:
            send_to_elasticsearch("domains", r, delete=True)
            continue
        try:
            props = calced_props(domain_obj, r["_id"], all_stats)
            if props['cp_first_form'] is None:
                del props['cp_first_form']
            if props['cp_last_form'] is None:
                del props['cp_last_form']
            if props['cp_300th_form'] is None:
                del props['cp_300th_form']
            send_to_elasticsearch("domains", props, es_merge_update=True)
        except Exception as e:
            notify_exception(None, message='Domain {} failed on stats calculations with {}'.format(dom, e))


def get_domains_to_update_es_filter():
    """
    Returns ES filter to filter domains that are never updated or
        domains that haven't been updated since a week or domains that
        have been updated within last week but have new form submissions
        in the last day.
    """
    last_week = datetime.utcnow() - timedelta(days=7)
    more_than_a_week_ago = filters.date_range('cp_last_updated', lt=last_week)
    less_than_a_week_ago = filters.date_range('cp_last_updated', gte=last_week)
    not_updated = filters.missing('cp_last_updated')
    domains_submitted_today = (FormES().submitted(gte=datetime.utcnow() - timedelta(days=1))
        .terms_aggregation('domain', 'domain').size(0).run().aggregations.domain.keys)
    return filters.OR(
        not_updated,
        more_than_a_week_ago,
        filters.AND(less_than_a_week_ago, filters.term('name', domains_submitted_today))
    )


def is_app_active(app_id, domain):
    return app_has_been_submitted_to_in_last_30_days(domain, app_id)


@periodic_task(run_every=crontab(hour="2", minute="0", day_of_week="*"), queue='background_queue')
def apps_update_calculated_properties():
    es = get_es_new()
    q = {"filter": {"and": [{"missing": {"field": "copy_of"}}]}}
    results = stream_es_query(q=q, es_index='apps', size=999999, chunksize=500)
    for r in results:
        props = {"cp_is_active": is_app_active(r["_id"], r["_source"]["domain"])}
        es.update(APP_INDEX, ES_META['apps'].type, r["_id"], body={"doc": props})


@task(serializer='pickle', ignore_result=True)
def export_all_rows_task(ReportClass, report_state, recipient_list=None, subject=None):
    report = object.__new__(ReportClass)
    report.__setstate__(report_state)
    report.rendered_as = 'export'

    setattr(report.request, 'REQUEST', {})
    file = report.excel_response
    report_class = report.__class__.__module__ + '.' + report.__class__.__name__
    hash_id = _store_excel_in_blobdb(report_class, file, report.domain)
    if not recipient_list:
        recipient_list = [report.request.couch_user.get_email()]
    for recipient in recipient_list:
        _send_email(report.request.couch_user, report, hash_id, recipient=recipient, subject=subject)


def _send_email(user, report, hash_id, recipient, subject=None):
    domain = report.domain or user.get_domains()[0]
    link = absolute_reverse("export_report", args=[domain, str(hash_id),
                                                   report.export_format])

    send_report_download_email(report.name, recipient, link, subject)


def _store_excel_in_blobdb(report_class, file, domain):

    key = uuid.uuid4().hex
    expired = 60 * 24 * 7  # 7 days
    db = get_blob_db()

    kw = {
        "domain": domain,
        "parent_id": key,
        "type_code": CODES.tempfile,
        "key": key,
        "timeout": expired,
        "properties": {"report_class": report_class}
    }
    file.seek(0)
    db.put(file, **kw)
    return key


@task(serializer='pickle')
def build_form_multimedia_zip(
        domain,
        export_id,
        datespan,
        user_types,
        download_id,
):
    from corehq.apps.export.models import FormExportInstance
    export = FormExportInstance.get(export_id)
    form_ids = get_form_ids_having_multimedia(
        domain, export.app_id, export.xmlns, datespan, user_types
    )
    forms_info = _get_form_attachment_info(domain, form_ids, export)

    num_forms = len(forms_info)
    DownloadBase.set_progress(build_form_multimedia_zip, 0, num_forms)

    all_case_ids = set.union(*(info['case_ids'] for info in forms_info))
    case_id_to_name = _get_case_names(domain, all_case_ids)

    with TransientTempfile() as temp_path:
        with open(temp_path, 'wb') as f:
            _write_attachments_to_file(temp_path, num_forms, forms_info, case_id_to_name)
        with open(temp_path, 'rb') as f:
            zip_name = 'multimedia-{}'.format(unidecode(export.name))
            _save_and_expose_zip(f, zip_name, domain, download_id)

    DownloadBase.set_progress(build_form_multimedia_zip, num_forms, num_forms)


def _get_form_attachment_info(domain, form_ids, export):
    properties = _get_export_properties(export)
    return [
        _extract_form_attachment_info(form, properties)
        for form in FormAccessors(domain).iter_forms(form_ids)
    ]


def _get_case_names(domain, case_ids):
    case_id_to_name = {c: c for c in case_ids}
    for case in scroll_case_names(domain, case_ids):
        if case.get('name'):
            case_id_to_name[case.get('_id')] = case.get('name')
    return case_id_to_name


def _format_filename(form_info, question_id, extension, case_id_to_name):
    filename = "{}-{}-form_{}{}".format(
        unidecode(question_id),
        form_info['username'] or form_info['form'].user_id or 'user_unknown',
        form_info['form'].form_id or 'unknown',
        extension
    )
    if form_info['case_ids']:
        case_names = '-'.join(map(
            lambda case_id: case_id_to_name[case_id],
            form_info['case_ids'],
        ))
        filename = '{}-{}'.format(case_names, filename)
    return filename


def _write_attachments_to_file(fpath, num_forms, forms_info, case_id_to_name):
    total_size = 0
    with open(fpath, 'wb') as zfile:
        with zipfile.ZipFile(zfile, 'w') as multimedia_zipfile:
            for form_number, form_info in enumerate(forms_info, 1):
                form = form_info['form']
                for attachment in form_info['attachments']:
                    total_size += attachment['size']
                    if total_size >= MAX_MM_ZIP_GBS * 1024**3:
                        raise Exception("Refusing to make multimedia export bigger than {} GB"
                                        .format(MAX_MM_ZIP_GBS))
                    filename = _format_filename(
                        form_info,
                        attachment['question_id'],
                        attachment['extension'],
                        case_id_to_name
                    )
                    zip_info = zipfile.ZipInfo(filename, attachment['timestamp'])
                    multimedia_zipfile.writestr(zip_info, form.get_attachment(
                        attachment['name']),
                        zipfile.ZIP_STORED
                    )
                DownloadBase.set_progress(build_form_multimedia_zip, form_number, num_forms)


def _save_and_expose_zip(f, zip_name, domain, download_id):
    expiry_minutes = 60
    get_blob_db().put(
        f,
        key=download_id,
        domain=domain,
        parent_id=domain,
        type_code=CODES.form_multimedia,
        timeout=expiry_minutes,
    )
    expose_blob_download(
        download_id,
        expiry=expiry_minutes * 60,  # seconds
        mimetype='application/zip',
        content_disposition=safe_filename_header(zip_name, 'zip'),
        download_id=download_id,
    )



def _convert_legacy_indices_to_export_properties(indices):
    # Strip the prefixed 'form' and change '.'s to '-'s
    return set(map(
        lambda index: '-'.join(index.split('.')[1:]),
        # Filter out any columns that are not form questions
        list(filter(
            lambda index: index and index.startswith('form'),
            indices,
        )),
    ))


def _get_export_properties(export):
    """
    Return a list of strings corresponding to form questions that are
    included in the export.
    """
    properties = set()
    for table in export.tables:
        for column in table.columns:
            if column.selected and column.item:
                path_parts = [n.name for n in column.item.path]
                path_parts = path_parts[1:] if path_parts[0] == "form" else path_parts
                properties.add("-".join(path_parts))
    return properties


def _extract_form_attachment_info(form, properties):
    """
    This is a helper function for build_form_multimedia_zip.
    Return a dict containing information about the given form and its relevant
    attachments
    """
    def find_question_id(form, value):
        for k, v in six.iteritems(form):
            if isinstance(v, dict):
                ret = find_question_id(v, value)
                if ret:
                    return [k] + ret
            elif isinstance(v, list):
                for repeat in v:
                    ret = find_question_id(repeat, value)
                    if ret:
                        return [k] + ret
            else:
                if v == value:
                    return [k]

        return None

    unknown_number = 0

    case_blocks = extract_case_blocks(form.form_data)
    form_info = {
        'form': form,
        'attachments': [],
        'case_ids': {c['@case_id'] for c in case_blocks},
        'username': form.get_data('form/meta/username')
    }

    # TODO make form.attachments always return objects that conform to a
    # uniform interface. XFormInstance attachment values are dicts, and
    # XFormInstanceSQL attachment values are BlobMeta objects.
    for attachment_name, attachment in six.iteritems(form.attachments):
        if hasattr(attachment, 'content_type'):
            content_type = attachment.content_type
        else:
            content_type = attachment['content_type']
        if content_type == 'text/xml':
            continue
        try:
            question_id = six.text_type(
                '-'.join(find_question_id(form.form_data, attachment_name)))
        except TypeError:
            question_id = 'unknown' + six.text_type(unknown_number)
            unknown_number += 1

        if not properties or question_id in properties:
            extension = six.text_type(os.path.splitext(attachment_name)[1])
            if hasattr(attachment, 'content_length'):
                # BlobMeta
                size = attachment.content_length
            elif 'content_length' in attachment:
                # dict from BlobMeta.to_json()
                size = attachment['content_length']
            else:
                # couch attachment dict
                size = attachment['length']
            form_info['attachments'].append({
                'size': size,
                'name': attachment_name,
                'question_id': question_id,
                'extension': extension,
                'timestamp': form.received_on.timetuple(),
            })

    return form_info
