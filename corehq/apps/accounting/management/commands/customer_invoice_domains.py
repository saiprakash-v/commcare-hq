from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import csv342 as csv
from io import open

from django.core.management import BaseCommand

from corehq.apps.accounting.invoicing import SmsLineItemFactory, UserLineItemFactory
from corehq.apps.accounting.models import CustomerInvoice, SoftwarePlanVersion, FeatureType, LineItem


class Command(BaseCommand):

    def handle(self, *args, **options):
        with open('customer_invoices.csv', 'w') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Customer Invoice ID',
                'Account Name',
                'Line Item Plan Edition',
                'Original Number of Users',
                'Original SMS Cost',
                'Correct Number of Users',
                'Correct SMS Cost',
                'Issue?',
            ])

            for invoice in CustomerInvoice.objects.order_by('id'):
                invoice_id = invoice.id
                for plan_version in SoftwarePlanVersion.objects.filter(
                    id__in=invoice.subscriptions.values('plan_version__id')
                ):
                    sms_rate = plan_version.feature_rates.get(feature__feature_type=FeatureType.SMS)
                    user_rate = plan_version.feature_rates.get(feature__feature_type=FeatureType.USER)
                    sample_subscription = invoice.subscriptions.filter(plan_version=plan_version)[0]  # any will do
                    sms_factory = SmsLineItemFactory(
                        sample_subscription,
                        sms_rate,
                        invoice
                    )
                    user_factory = UserLineItemFactory(
                        sample_subscription,
                        user_rate,
                        invoice
                    )
                    try:
                        writer.writerow([
                            invoice_id,
                            invoice.account.name,
                            plan_version.plan.edition,
                            invoice.lineitem_set.get(feature_rate=user_rate).quantity,
                            invoice.lineitem_set.get(feature_rate=sms_rate).unit_cost,
                            user_factory.num_excess_users_over_period,
                            sms_factory.unit_cost,
                            'N' if (
                                invoice.lineitem_set.get(feature_rate=user_rate).quantity,
                                invoice.lineitem_set.get(feature_rate=sms_rate).unit_cost,
                            ) == (
                                user_factory.num_excess_users_over_period,
                                sms_factory.unit_cost,
                            ) else 'Y',
                        ])
                    except LineItem.MultipleObjectsReturned:
                        print(invoice_id)
