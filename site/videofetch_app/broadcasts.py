from __future__ import annotations

from typing import Iterable

from django.db import transaction
from django.utils import timezone

from bot.broadcast_media import build_broadcast_send_plan
from videofetch_app.models import Broadcast, BroadcastDelivery, TelegramAccount


QUEUEABLE_BROADCAST_STATUSES = {
    Broadcast.Status.DRAFT,
    Broadcast.Status.FAILED,
    Broadcast.Status.CANCELED,
}


def queue_broadcast(*, broadcast: Broadcast) -> Broadcast:
    with transaction.atomic():
        locked = (
            Broadcast.objects.select_for_update()
            .prefetch_related('attachments')
            .get(pk=broadcast.pk)
        )

        if locked.status not in QUEUEABLE_BROADCAST_STATUSES:
            raise ValueError('Broadcast is not queueable in the current status.')

        if not locked.text.strip() and not locked.attachments.exists():
            raise ValueError('Broadcast must contain text or at least one attachment.')

        build_broadcast_send_plan(locked.text, list(locked.attachments.all()))

        recipients = _load_recipients(locked)
        BroadcastDelivery.objects.filter(broadcast=locked).delete()

        deliveries = [
            BroadcastDelivery(
                broadcast=locked,
                recipient_user=recipient.user,
                telegram_user_id=recipient.telegram_user_id,
                chat_id=recipient.chat_id,
                status=BroadcastDelivery.Status.PENDING,
            )
            for recipient in recipients
        ]

        if deliveries:
            BroadcastDelivery.objects.bulk_create(deliveries, batch_size=1000)
            locked.status = Broadcast.Status.QUEUED
            locked.total_recipients = len(deliveries)
            locked.sent_count = 0
            locked.failed_count = 0
            locked.started_at = None
            locked.finished_at = None
            locked.last_error = ''
        else:
            locked.status = Broadcast.Status.FAILED
            locked.total_recipients = 0
            locked.sent_count = 0
            locked.failed_count = 0
            locked.started_at = timezone.now()
            locked.finished_at = timezone.now()
            locked.last_error = 'No Telegram recipients found.'

        locked.save(
            update_fields=[
                'status',
                'total_recipients',
                'sent_count',
                'failed_count',
                'started_at',
                'finished_at',
                'last_error',
                'updated_at',
            ]
        )
        return locked


def _load_recipients(broadcast: Broadcast) -> list[TelegramAccount]:
    recipients = TelegramAccount.objects.select_related('user').order_by('user_id')
    if broadcast.recipient_mode == Broadcast.RecipientMode.MARKETING_OPT_IN:
        recipients = recipients.filter(user__marketing_opt_in=True)
    return list(recipients)


def store_uploaded_attachments(*, broadcast: Broadcast, files: Iterable) -> int:
    created = 0
    for uploaded in files:
        if not uploaded:
            continue
        attachment = broadcast.attachments.create(
            file=uploaded,
            original_name=(getattr(uploaded, 'name', '') or '')[:255],
            content_type=(getattr(uploaded, 'content_type', '') or '')[:255],
            size_bytes=int(getattr(uploaded, 'size', 0) or 0),
        )
        if not attachment.original_name:
            attachment.original_name = attachment.file.name.rsplit('/', 1)[-1]
            attachment.save(update_fields=['original_name'])
        created += 1
    return created
