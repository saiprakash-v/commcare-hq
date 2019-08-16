from __future__ import absolute_import, unicode_literals

from corehq.apps.es.users import UserES


def get_mobile_users(domains):
    return set(
        UserES()
        .show_inactive()
        .mobile_users()
        .domain(domains)
        .scroll_ids()
    )
