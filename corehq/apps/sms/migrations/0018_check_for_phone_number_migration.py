from django.db import migrations

from corehq.apps.sms.migration_status import (
    assert_phone_number_migration_complete,
)


def noop(*args, **kwargs):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sms', '0017_update_phoneblacklist'),
    ]

    operations = {
        migrations.RunPython(assert_phone_number_migration_complete, reverse_code=noop),
    }
