from django.db import models
from django.db.models.functions import Now
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

class User(models.Model):
    id = models.BigAutoField(primary_key=True, db_column='id')
    created_at = models.DateTimeField(db_default=Now())
    marketing_opt_in = models.BooleanField(db_default=False)

    class Meta:
        db_table = 'users'
        managed = False


class TelegramAccount(models.Model):
    user = models.OneToOneField(
        User,
        primary_key=True,
        db_column='user_id',
        on_delete=models.CASCADE,
        related_name='telegram_account',
    )
    telegram_user_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField()
    username = models.TextField(null=True)
    first_name = models.TextField(null=True)
    last_name = models.TextField(null=True)
    language_code = models.TextField(null=True)
    last_seen_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = 'telegram_users'
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


class Broadcast(models.Model):
    class RecipientMode(models.TextChoices):
        ALL_TELEGRAM = 'all_telegram', 'All Telegram users'
        MARKETING_OPT_IN = 'marketing_opt_in', 'Only marketing opt-in'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        CANCELED = 'canceled', 'Canceled'

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='broadcasts',
    )
    title = models.CharField(max_length=200, blank=True, default='')
    text = models.TextField(blank=True, default='')
    recipient_mode = models.CharField(
        max_length=32,
        choices=RecipientMode.choices,
        default=RecipientMode.ALL_TELEGRAM,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'broadcasts'
        ordering = ['-created_at']
        managed = False

    def __str__(self) -> str:
        return self.title or f'Broadcast #{self.pk}'


class BroadcastAttachment(models.Model):
    id = models.BigAutoField(primary_key=True)
    broadcast = models.ForeignKey(
        Broadcast,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    file = models.FileField(upload_to='broadcasts/%Y/%m/%d')
    original_name = models.CharField(max_length=255, blank=True, default='')
    content_type = models.CharField(max_length=255, blank=True, default='')
    size_bytes = models.BigIntegerField(default=0)

    class Meta:
        db_table = 'broadcast_attachments'
        ordering = ['id']
        managed = False

    def __str__(self) -> str:
        return self.original_name or self.file.name


class BroadcastDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        RUNNING = 'running', 'Running'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    id = models.BigAutoField(primary_key=True)
    broadcast = models.ForeignKey(
        Broadcast,
        on_delete=models.CASCADE,
        related_name='deliveries',
    )
    recipient_user = models.ForeignKey(
        User,
        db_column='recipient_user_id',
        on_delete=models.CASCADE,
        related_name='broadcast_deliveries',
    )
    telegram_user_id = models.BigIntegerField()
    chat_id = models.BigIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField(db_default=Now())
    locked_by = models.TextField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'broadcast_deliveries'
        managed = False
        constraints = [
            models.UniqueConstraint(
                fields=['broadcast', 'recipient_user'],
                name='broadcast_delivery_unique_recipient',
            )
        ]
