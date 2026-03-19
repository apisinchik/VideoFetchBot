from django import forms
from django.utils.translation import gettext_lazy as _

from videofetch_app.models import Broadcast


class UrlSender(forms.Form):
    template_name = 'forms/url_sender.html'
    url = forms.URLField(
        error_messages={
            'required': _('Введите ссылку'),
            'invalid': _('В ссылке допущена ошибка'),
        }
    )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data]
        if not data:
            return []
        return [clean_one(data, initial)]


class BroadcastAdminForm(forms.ModelForm):
    upload_files = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True}),
        help_text=_('Можно выбрать сразу несколько файлов.'),
        label=_('Новые файлы'),
    )

    class Meta:
        model = Broadcast
        fields = ['title', 'text', 'recipient_mode']
