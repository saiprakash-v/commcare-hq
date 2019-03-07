from __future__ import absolute_import
from __future__ import unicode_literals

import tempfile

import six

from corehq.apps.app_manager.models import LinkedApplication
from corehq import toggles
from corehq.apps.translations.const import MODULES_AND_FORMS_SHEET_NAME


def get_file_content_from_workbook(wb):
    # temporary write the in-memory workbook to be able to read its content
    with tempfile.TemporaryFile(suffix='.xlsx') as f:
        wb.save(f)
        f.seek(0)
        content = f.read()
    return content


def update_app_translations_from_trans_dict(app, trans_dict):
    if toggles.PARTIAL_UI_TRANSLATIONS.enabled(app.domain):
        if isinstance(app, LinkedApplication):
            for lang, trans in six.iteritems(app.translations):
                if lang in trans_dict:
                    app.translations[lang].update(trans_dict[lang])

        for lang, trans in six.iteritems(app.translations):
            if lang in trans_dict:
                app.translations[lang].update(trans_dict[lang])
    else:
        if isinstance(app, LinkedApplication):
            app.linked_app_translations.update(trans_dict)
        app.translations.update(trans_dict)


def is_form_sheet(sheet):
    return 'module' in sheet.worksheet.title and 'form' in sheet.worksheet.title


def is_module_sheet(sheet):
    return 'module' in sheet.worksheet.title and 'form' not in sheet.worksheet.title


def is_modules_and_forms_sheet(sheet):
    return sheet.worksheet.title == MODULES_AND_FORMS_SHEET_NAME
