from rest_framework import serializers

from videofetcher.url_safety import is_public_http_url


class AnalyzeRequestSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2000)

    def validate_url(self, value: str) -> str:
        value = (value or "").strip()
        if not is_public_http_url(value):
            raise serializers.ValidationError("Разрешены только публичные http(s)-ссылки.")
        return value


class StartJobRequestSerializer(serializers.Serializer):
    format_index = serializers.IntegerField(min_value=0)
    audio_index = serializers.IntegerField(required=False, allow_null=True, min_value=0)
