from django import forms
from django.utils.translation import gettext_lazy as _

class UrlSender(forms.Form):
    template_name = 'forms/url_sender.html'
    url = forms.URLField(
        error_messages={
            'required': _('Введите ссылку'),
            'invalid': _('В ссылке допущена ошибка'),
        }
    )