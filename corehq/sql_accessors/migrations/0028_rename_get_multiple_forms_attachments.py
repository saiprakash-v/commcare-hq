from django.db import migrations
from corehq.form_processor.models import CaseTransaction
from corehq.sql_db.operations import RawSQLMigration


migrator = RawSQLMigration(('corehq', 'sql_accessors', 'sql_templates'), {
    'TRANSACTION_TYPE_FORM': CaseTransaction.TYPE_FORM
})


class Migration(migrations.Migration):

    dependencies = [
        ('sql_accessors', '0027_audit_fixups'),
    ]

    operations = [
        migrator.get_migration('get_multiple_forms_attachments.sql'),
    ]
