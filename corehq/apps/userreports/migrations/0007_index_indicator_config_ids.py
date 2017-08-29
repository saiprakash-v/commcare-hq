# -*- coding: utf-8 -*-
# Generated by Django 1.10.7 on 2017-07-10 14:07
from __future__ import unicode_literals

import django.contrib.postgres.fields
from django.db import migrations, models
from corehq.sql_db.operations import HqRunPython, HqRunSQL
from corehq.util.django_migrations import add_if_not_exists_raw


class Migration(migrations.Migration):

    dependencies = [
        ('userreports', '0006_add_index_to_domain'),
    ]

    operations = [
        HqRunSQL(
            add_if_not_exists_raw(
                """
                CREATE INDEX userreports_asyncindicator_indicator_config_ids_gin_idx
                ON userreports_asyncindicator USING GIN (indicator_config_ids)
                """, name='userreports_asyncindicator_indicator_config_ids_gin_idx'
            ),
            reverse_sql=
            """
            DROP INDEX IF EXISTS userreports_asyncindicator_indicator_config_ids_gin_idx
            """,
        ),
    ]
