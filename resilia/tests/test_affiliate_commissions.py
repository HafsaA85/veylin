
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import TestCase, RequestFactory, override_settings

from resilia.models import Subscription, Affiliate, AffiliateCommission
from resilia.views import stripe_webhook
from datetime import timedelta
from django.utils import timezone

@override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
class AffiliateCommissionTests(TestCase):
    def setUp(self):
        self.affiliate_user = User.objects.create_user(
            username="affiliate_test",
            password="test-password",
        )
        self.affiliate = Affiliate.objects.get(user=self.affiliate_user)

        self.customer_user = User.objects.create_user(
            username="referred_test",
            password="test-password",
        )
        self.subscription = Subscription.objects.create(
            user=self.customer_user,
            referred_by=self.affiliate,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
        )

        self.factory = RequestFactory()

    def test_stripe_invoice_lookup_failure_returns_500(self):
        from stripe import StripeError

        invoice = {
            "id": "in_api_error",
            "customer": "cus_test_123",
            "subscription": "sub_test_123",
            "amount_paid": 1999,
            "currency": "gbp",
            "created": 1000,
        }
        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": invoice},
        }
        request = self.factory.post(
            "/stripe/webhook/",
            data=b"test-payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        with (
            patch(
                "resilia.views.stripe.Webhook.construct_event",
                return_value=event,
            ),
            patch(
                "resilia.views.stripe.Invoice.list",
                side_effect=StripeError("Simulated Stripe API failure"),
            ),
        ):
            response = stripe_webhook(request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(AffiliateCommission.objects.count(), 0)


    def test_current_invoice_missing_from_paid_list_returns_500(self):
        invoice = {
            "id": "in_not_yet_listed",
            "customer": "cus_test_123",
            "subscription": "sub_test_123",
            "amount_paid": 1999,
            "currency": "gbp",
            "created": 1000,
        }
        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": invoice},
        }
        request = self.factory.post(
            "/stripe/webhook/",
            data=b"test-payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        with (
            patch(
                "resilia.views.stripe.Webhook.construct_event",
                return_value=event,
            ),
            patch("resilia.views.stripe.Invoice.list") as invoice_list,
        ):
            invoice_list.return_value.auto_paging_iter.return_value = []
            response = stripe_webhook(request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(AffiliateCommission.objects.count(), 0)

    def send_invoice_event(
        self,
        invoice_id="in_test_123",
        amount_paid=1999,
        currency="gbp",
        subscription_id="sub_test_123",
    ):
        invoice = {
            "id": invoice_id,
            "customer": "cus_test_123",
            "subscription": subscription_id,
            "amount_paid": amount_paid,
            "currency": currency,
            "created": 1000,
        }

        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": invoice},
        }

        request = self.factory.post(
            "/stripe/webhook/",
            data=b"test-payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        with (
            patch(
                "resilia.views.stripe.Webhook.construct_event",
                return_value=event,
            ),
            patch("resilia.views.stripe.Invoice.list") as invoice_list,
        ):
            invoice_list.return_value.auto_paging_iter.return_value = [
                invoice
            ]
            response = stripe_webhook(request)

        return response

    def test_first_paid_invoice_records_20_percent_commission(self):
        response = self.send_invoice_event()

        self.assertEqual(response.status_code, 200)
        commission = AffiliateCommission.objects.get(
            referred_subscription=self.subscription
        )
        self.assertEqual(commission.payment_amount, Decimal("19.99"))
        self.assertEqual(commission.commission_amount, Decimal("4.00"))


    def test_first_paid_invoice_after_trial_records_commission(self):
        # Simulate a subscription that has completed its free trial.
        self.subscription.has_used_trial = True
        self.subscription.trial_start = timezone.now() - timedelta(days=8)
        self.subscription.save()

        response = self.send_invoice_event(
            invoice_id="in_after_trial",
            amount_paid=1999,
        )

        self.assertEqual(response.status_code, 200)

        commission = AffiliateCommission.objects.get(
            referred_subscription=self.subscription
        )
        self.assertEqual(commission.payment_amount, Decimal("19.99"))
        self.assertEqual(commission.commission_amount, Decimal("4.00"))

    def test_zero_value_invoice_does_not_record_commission(self):
        self.send_invoice_event(amount_paid=0)

        self.assertEqual(AffiliateCommission.objects.count(), 0)

    def test_duplicate_invoice_does_not_create_duplicate_commission(self):
        self.send_invoice_event()
        self.send_invoice_event()

        self.assertEqual(
            AffiliateCommission.objects.filter(
                referred_subscription=self.subscription
            ).count(),
            1,
        )

    def test_second_paid_invoice_does_not_create_another_commission(self):
        self.send_invoice_event(invoice_id="in_first", amount_paid=1999)

        second_invoice = {
            "id": "in_second",
            "customer": "cus_test_123",
            "subscription": "sub_test_123",
            "amount_paid": 1999,
            "currency": "gbp",
            "created": 2000,
        }

        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": second_invoice},
        }
        request = self.factory.post(
            "/stripe/webhook/",
            data=b"test-payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        with (
            patch(
                "resilia.views.stripe.Webhook.construct_event",
                return_value=event,
            ),
            patch("resilia.views.stripe.Invoice.list") as invoice_list,
        ):
            invoice_list.return_value.auto_paging_iter.return_value = [
                {
                    "id": "in_first",
                    "amount_paid": 1999,
                    "currency": "gbp",
                    "created": 1000,
                },
                second_invoice,
            ]
            stripe_webhook(request)

        self.assertEqual(
            AffiliateCommission.objects.filter(
                referred_subscription=self.subscription
            ).count(),
            1,
        )


    def test_commission_uses_payment_time_not_invoice_creation_time(self):
        older_created_invoice = {
            "id": "in_created_first_paid_second",
            "customer": "cus_test_123",
            "subscription": "sub_test_123",
            "amount_paid": 1999,
            "currency": "gbp",
            "created": 1000,
            "status_transitions": {"paid_at": 3000},
        }
        newer_created_invoice = {
            "id": "in_created_second_paid_first",
            "customer": "cus_test_123",
            "subscription": "sub_test_123",
            "amount_paid": 1999,
            "currency": "gbp",
            "created": 2000,
            "status_transitions": {"paid_at": 2500},
        }

        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": newer_created_invoice},
        }
        request = self.factory.post(
            "/stripe/webhook/",
            data=b"test-payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        with (
            patch(
                "resilia.views.stripe.Webhook.construct_event",
                return_value=event,
            ),
            patch("resilia.views.stripe.Invoice.list") as invoice_list,
        ):
            invoice_list.return_value.auto_paging_iter.return_value = [
                older_created_invoice,
                newer_created_invoice,
            ]
            response = stripe_webhook(request)

        self.assertEqual(response.status_code, 200)
        commission = AffiliateCommission.objects.get()
        self.assertEqual(
            commission.stripe_invoice_id,
            "in_created_second_paid_first",
        )
        self.assertEqual(commission.commission_amount, Decimal("4.00"))


    def test_invoice_recovers_subscription_from_metadata(self):
        self.subscription.stripe_customer_id = ""
        self.subscription.save()

        invoice = {
            "id": "in_metadata_fallback",
            "customer": "cus_test_123",
            "subscription": "sub_test_123",
            "amount_paid": 1999,
            "currency": "gbp",
            "created": 1000,
            "parent": {
                "subscription_details": {
                    "subscription": "sub_test_123",
                    "metadata": {
                        "user_id": str(self.customer_user.id),
                    },
                },
            },
        }

        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": invoice},
        }

        request = self.factory.post(
            "/stripe/webhook/",
            data=b"test-payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )


        with (
            patch(
                "resilia.views.stripe.Webhook.construct_event",
                return_value=event,
            ),
            patch(
                "resilia.views.stripe.Subscription.retrieve",
                return_value={
                    "id": "sub_test_123",
                    "metadata": {
                        "user_id": str(self.customer_user.id),
                    },
                },
            ),
            patch("resilia.views.stripe.Invoice.list") as invoice_list,
        ):

            invoice_list.return_value.auto_paging_iter.return_value = [
                invoice
            ]
            response = stripe_webhook(request)

        self.assertEqual(response.status_code, 200)

        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.stripe_customer_id,
            "cus_test_123",
        )
        self.assertEqual(AffiliateCommission.objects.count(), 1)

        commission = AffiliateCommission.objects.get()
        self.assertEqual(commission.commission_amount, Decimal("4.00"))
