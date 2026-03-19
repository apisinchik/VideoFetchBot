from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse

from videofetch_app.broadcasts import QUEUEABLE_BROADCAST_STATUSES, queue_broadcast, store_uploaded_attachments
from videofetch_app.forms import BroadcastAdminForm
from videofetch_app.models import (
    AnalyseSlot,
    Broadcast,
    BroadcastAttachment,
    BroadcastDelivery,
    Job,
    TelegramAccount,
    User,
)


admin.site.site_header = 'VideoFetch Admin'
admin.site.site_title = 'VideoFetch Admin'
admin.site.index_title = 'Operations'


def _human_size(size_bytes: int | None) -> str:
    size = int(size_bytes or 0)
    units = ['B', 'KB', 'MB', 'GB']
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == 'B':
                return f'{int(value)} {unit}'
            return f'{value:.1f} {unit}'
        value /= 1024.0
    return f'{size} B'


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'marketing_opt_in')
    list_filter = ('marketing_opt_in', 'created_at')
    search_fields = ('id',)
    readonly_fields = ('id', 'created_at')
    ordering = ('-id',)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'telegram_user_id',
        'chat_id',
        'username',
        'first_name',
        'last_name',
        'last_seen_at',
    )
    search_fields = ('telegram_user_id', 'chat_id', 'username', 'first_name', 'last_name')
    readonly_fields = (
        'user',
        'telegram_user_id',
        'chat_id',
        'username',
        'first_name',
        'last_name',
        'language_code',
        'last_seen_at',
    )
    ordering = ('-last_seen_at',)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_by_user',
        'created_via',
        'status',
        'progress',
        'stage',
        'title',
        'requested_quality',
        'requested_audio',
        'created_at',
    )
    list_filter = ('created_via', 'status', 'is_short', 'created_at')
    search_fields = ('id', 'title', 'source_url', 'requested_quality', 'requested_audio')
    readonly_fields = [field.name for field in Job._meta.fields]
    ordering = ('-created_at',)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(AnalyseSlot)
class AnalyseSlotAdmin(admin.ModelAdmin):
    list_display = ('slot_id', 'holder', 'lease_until')
    readonly_fields = ('slot_id',)
    ordering = ('slot_id',)


class BroadcastAttachmentInline(admin.TabularInline):
    model = BroadcastAttachment
    extra = 0
    fields = ('file', 'original_name', 'content_type', 'size_bytes_display')
    readonly_fields = ('file', 'original_name', 'content_type', 'size_bytes_display')

    @admin.display(description='Size')
    def size_bytes_display(self, obj: BroadcastAttachment) -> str:
        return _human_size(obj.size_bytes)

    def has_add_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        return True

    def has_view_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        return True

    def has_delete_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        if obj is None:
            return False
        return obj.status in QUEUEABLE_BROADCAST_STATUSES


class BroadcastDeliveryInline(admin.TabularInline):
    model = BroadcastDelivery
    extra = 0
    can_delete = False
    fields = (
        'recipient_user',
        'telegram_user_id',
        'chat_id',
        'status',
        'attempts',
        'sent_at',
        'last_error',
    )
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        return False

    def has_view_permission(self, request: HttpRequest, obj: Broadcast | None = None) -> bool:
        return True


@admin.action(description='Queue selected broadcasts')
def queue_selected_broadcasts(modeladmin, request: HttpRequest, queryset):
    queued = 0
    skipped = 0
    for broadcast in queryset:
        try:
            queue_broadcast(broadcast=broadcast)
            queued += 1
        except ValueError:
            skipped += 1

    if queued:
        modeladmin.message_user(request, f'Queued broadcasts: {queued}', level=messages.SUCCESS)
    if skipped:
        modeladmin.message_user(request, f'Skipped broadcasts: {skipped}', level=messages.WARNING)


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    form = BroadcastAdminForm
    change_form_template = 'admin/videofetch_app/broadcast/change_form.html'
    actions = (queue_selected_broadcasts,)
    inlines = (BroadcastAttachmentInline, BroadcastDeliveryInline)
    save_as = True
    list_display = (
        'id',
        'title',
        'recipient_mode',
        'status',
        'total_recipients',
        'sent_count',
        'failed_count',
        'created_at',
        'started_at',
        'finished_at',
    )
    list_filter = ('status', 'recipient_mode', 'created_at')
    search_fields = ('title', 'text', 'id')
    readonly_fields = (
        'created_by',
        'status',
        'total_recipients',
        'sent_count',
        'failed_count',
        'started_at',
        'finished_at',
        'last_error',
        'created_at',
        'updated_at',
    )
    fieldsets = (
        (None, {'fields': ('title', 'text', 'recipient_mode', 'upload_files')}),
        (
            'Delivery',
            {
                'fields': (
                    'created_by',
                    'status',
                    'total_recipients',
                    'sent_count',
                    'failed_count',
                    'started_at',
                    'finished_at',
                    'last_error',
                    'created_at',
                    'updated_at',
                )
            },
        ),
    )

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).prefetch_related('attachments')

    def get_readonly_fields(self, request: HttpRequest, obj: Broadcast | None = None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.status not in QUEUEABLE_BROADCAST_STATUSES:
            readonly.extend(['title', 'text', 'recipient_mode'])
        return readonly

    def save_model(self, request: HttpRequest, obj: Broadcast, form, change: bool) -> None:
        if not obj.created_by_id and request.user.is_authenticated:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request: HttpRequest, form, formsets, change: bool) -> None:
        super().save_related(request, form, formsets, change)
        if form.instance.status not in QUEUEABLE_BROADCAST_STATUSES:
            return
        uploaded_files = request.FILES.getlist('upload_files')
        created = store_uploaded_attachments(broadcast=form.instance, files=uploaded_files)
        if created:
            self.message_user(request, f'Uploaded files: {created}', level=messages.SUCCESS)

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        context['can_queue_broadcast'] = bool(add or (obj and obj.status in QUEUEABLE_BROADCAST_STATUSES))
        context['has_file_field'] = True
        return super().render_change_form(request, context, add, change, form_url, obj)

    def response_add(self, request: HttpRequest, obj: Broadcast, post_url_continue=None):
        if '_queuebroadcast' in request.POST:
            return self._queue_and_redirect(request, obj)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request: HttpRequest, obj: Broadcast):
        if '_queuebroadcast' in request.POST:
            return self._queue_and_redirect(request, obj)
        return super().response_change(request, obj)

    def _queue_and_redirect(self, request: HttpRequest, obj: Broadcast) -> HttpResponseRedirect:
        try:
            updated = queue_broadcast(broadcast=obj)
        except ValueError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                f'Broadcast #{updated.pk} queued for {updated.total_recipients} recipients.',
                level=messages.SUCCESS,
            )
        return HttpResponseRedirect(
            reverse('admin:videofetch_app_broadcast_change', args=[obj.pk])
        )
