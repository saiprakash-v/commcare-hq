from corehq.apps.sms.api import incoming
from corehq.apps.sms.views import IncomingBackendView
from corehq.messaging.smsbackends.starfish.models import StarfishBackend
from django.http import JsonResponse, HttpResponseBadRequest


class StarfishIncomingView(IncomingBackendView):
    urlname = 'starfish_sms_in'

    @property
    def backend_class(self):
        return StarfishBackend

    def clean_value(self, value):
        if isinstance(value, str):
            return value.strip()
        return value

    def get(self, request, api_key, *args, **kwargs):
        number = self.clean_value(request.GET.get("msisdn"))
        text = self.clean_value(request.GET.get("message"))
        if not number or not text:
            return HttpResponseBadRequest("MobileNumber or Text are missing")

        sms = incoming(
            number,
            text,
            StarfishBackend.get_api_id(),
            domain_scope=self.domain,
            backend_id=self.backend_couch_id,
        )
        return JsonResponse({"status": "OK", "message_id": sms.couch_id})

    def post(self, request, api_key, *args, **kwargs):
        return self.get(request, api_key, *args, **kwargs)
