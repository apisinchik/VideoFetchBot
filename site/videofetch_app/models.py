from django.db import models
from django.db.models.functions import Now

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

class User(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='id')
    created_at = models.DateTimeField(db_default=Now())
    marketing_opt_in = models.BooleanField(db_default=False)

    class Meta:
        db_table = 'users'
        managed = False


def progress_validator(value):
    if value < 0 or value > 100:
        raise ValidationError(
            _('%(value)s is invalid'),
            params={'value': value},
        )

class Job(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='id')
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    created_by_user = models.ForeignKey(User, 
                                        db_column='created_by_user_id', 
                                        on_delete=models.RESTRICT,
                                        )
    
    class CreatedBy(models.TextChoices):
        TELEGRAM = 'telegram'
        WEB = 'web'

    created_via = models.TextField(choices=CreatedBy, default=CreatedBy.WEB)

    telegram_user_id = models.BigIntegerField(null=True)
    telegram_chat_id = models.BigIntegerField(null=True)
    progress_msg_id = models.BigIntegerField(null=True)

    source_url = models.TextField()
    title = models.TextField(null=True)
    duration_seconds = models.IntegerField(null=True)
    is_short = models.BooleanField(db_default=False)

    requested_quality = models.TextField(null=True)
    requested_audio = models.TextField(null=True)
    selected_format = models.JSONField(null=True)
    selected_audio = models.JSONField(null=True)

    class Status(models.TextChoices):
        QUEUED = 'queued'
        RUNNING = 'running'
        DONE = 'done'
        FAILED = 'failed'
        CANCELED = 'canceled'

    status = models.TextField(choices=Status, default=Status.QUEUED)

    priority = models.IntegerField(db_default=0)
    progress = models.IntegerField(db_default=0, validators=[progress_validator])
    stage = models.TextField(null=True)

    attempts = models.IntegerField(db_default=0)
    run_after = models.DateTimeField(db_default=Now())
    locked_by = models.TextField(null=True)
    locked_at = models.DateTimeField(null=True)
    
    result_path = models.TextField(null=True)
    result_size_bytes = models.BigIntegerField(null=True)
    result_meta = models.JSONField(null=True)

    error_code = models.TextField(null=True)
    error_message = models.TextField(null=True)

    class Meta:
        db_table = 'download_jobs'
        managed = False


class AnalyseSlot(models.Model):
    slot_id = models.BigAutoField(primary_key=True, db_column='slot_id')

    class Holder(models.TextChoices):
        HOLD = 'hold'
        FREE = 'free'
    holder = models.TextField(choices=Holder, default=Holder.FREE)

    lease_until = models.DateTimeField(null=True)

    class Meta:
        db_table = 'analysis_slots'
        managed = False