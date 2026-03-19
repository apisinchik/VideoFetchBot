# Создано Django 6.0.1 2026-03-19

import django.db.models.deletion
import django.db.models.functions.datetime
from django.conf import settings
from django.db import migrations, models


SQL = """
CREATE TABLE IF NOT EXISTS broadcasts (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_id    INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,
    title            TEXT NOT NULL DEFAULT '',
    text             TEXT NOT NULL DEFAULT '',
    recipient_mode   TEXT NOT NULL CHECK (recipient_mode IN ('all_telegram','marketing_opt_in')) DEFAULT 'all_telegram',
    status           TEXT NOT NULL CHECK (status IN ('draft','queued','running','completed','failed','canceled')) DEFAULT 'draft',
    total_recipients INTEGER NOT NULL DEFAULT 0,
    sent_count       INTEGER NOT NULL DEFAULT 0,
    failed_count     INTEGER NOT NULL DEFAULT 0,
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    last_error       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS broadcast_attachments (
    id            BIGSERIAL PRIMARY KEY,
    broadcast_id  BIGINT NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    file          TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    content_type  TEXT NOT NULL DEFAULT '',
    size_bytes    BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS broadcast_deliveries (
    id                 BIGSERIAL PRIMARY KEY,
    broadcast_id       BIGINT NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    recipient_user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_user_id   BIGINT NOT NULL,
    chat_id            BIGINT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pending','running','sent','failed')) DEFAULT 'pending',
    attempts           INTEGER NOT NULL DEFAULT 0,
    run_after          TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by          TEXT,
    locked_at          TIMESTAMPTZ,
    sent_at            TIMESTAMPTZ,
    last_error         TEXT NOT NULL DEFAULT '',
    CONSTRAINT broadcast_delivery_unique_recipient UNIQUE (broadcast_id, recipient_user_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_status_created_at
    ON broadcasts(status, created_at);

CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_queue
    ON broadcast_deliveries(status, run_after, id);

CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_broadcast_id
    ON broadcast_deliveries(broadcast_id);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_broadcasts_updated_at ON broadcasts;
CREATE TRIGGER trg_broadcasts_updated_at
BEFORE UPDATE ON broadcasts
FOR EACH ROW EXECUTE PROCEDURE set_updated_at();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('videofetch_app', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(SQL, reverse_sql=migrations.RunSQL.noop),
            ],
            state_operations=[
                migrations.CreateModel(
                    name='TelegramAccount',
                    fields=[
                        ('user', models.OneToOneField(db_column='user_id', on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='telegram_account', serialize=False, to='videofetch_app.user')),
                        ('telegram_user_id', models.BigIntegerField(unique=True)),
                        ('chat_id', models.BigIntegerField()),
                        ('username', models.TextField(null=True)),
                        ('first_name', models.TextField(null=True)),
                        ('last_name', models.TextField(null=True)),
                        ('language_code', models.TextField(null=True)),
                        ('last_seen_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                    ],
                    options={
                        'db_table': 'telegram_users',
                        'managed': False,
                    },
                ),
                migrations.CreateModel(
                    name='Broadcast',
                    fields=[
                        ('id', models.BigAutoField(primary_key=True, serialize=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('title', models.CharField(blank=True, default='', max_length=200)),
                        ('text', models.TextField(blank=True, default='')),
                        ('recipient_mode', models.CharField(choices=[('all_telegram', 'All Telegram users'), ('marketing_opt_in', 'Only marketing opt-in')], default='all_telegram', max_length=32)),
                        ('status', models.CharField(choices=[('draft', 'Draft'), ('queued', 'Queued'), ('running', 'Running'), ('completed', 'Completed'), ('failed', 'Failed'), ('canceled', 'Canceled')], default='draft', max_length=16)),
                        ('total_recipients', models.PositiveIntegerField(default=0)),
                        ('sent_count', models.PositiveIntegerField(default=0)),
                        ('failed_count', models.PositiveIntegerField(default=0)),
                        ('started_at', models.DateTimeField(blank=True, null=True)),
                        ('finished_at', models.DateTimeField(blank=True, null=True)),
                        ('last_error', models.TextField(blank=True, default='')),
                        ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='broadcasts', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'db_table': 'broadcasts',
                        'ordering': ['-created_at'],
                        'managed': False,
                    },
                ),
                migrations.CreateModel(
                    name='BroadcastAttachment',
                    fields=[
                        ('id', models.BigAutoField(primary_key=True, serialize=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('file', models.FileField(upload_to='broadcasts/%Y/%m/%d')),
                        ('original_name', models.CharField(blank=True, default='', max_length=255)),
                        ('content_type', models.CharField(blank=True, default='', max_length=255)),
                        ('size_bytes', models.BigIntegerField(default=0)),
                        ('broadcast', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='videofetch_app.broadcast')),
                    ],
                    options={
                        'db_table': 'broadcast_attachments',
                        'ordering': ['id'],
                        'managed': False,
                    },
                ),
                migrations.CreateModel(
                    name='BroadcastDelivery',
                    fields=[
                        ('id', models.BigAutoField(primary_key=True, serialize=False)),
                        ('telegram_user_id', models.BigIntegerField()),
                        ('chat_id', models.BigIntegerField()),
                        ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('sent', 'Sent'), ('failed', 'Failed')], default='pending', max_length=16)),
                        ('attempts', models.PositiveIntegerField(default=0)),
                        ('run_after', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                        ('locked_by', models.TextField(blank=True, null=True)),
                        ('locked_at', models.DateTimeField(blank=True, null=True)),
                        ('sent_at', models.DateTimeField(blank=True, null=True)),
                        ('last_error', models.TextField(blank=True, default='')),
                        ('broadcast', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='videofetch_app.broadcast')),
                        ('recipient_user', models.ForeignKey(db_column='recipient_user_id', on_delete=django.db.models.deletion.CASCADE, related_name='broadcast_deliveries', to='videofetch_app.user')),
                    ],
                    options={
                        'db_table': 'broadcast_deliveries',
                        'managed': False,
                    },
                ),
                migrations.AddConstraint(
                    model_name='broadcastdelivery',
                    constraint=models.UniqueConstraint(fields=('broadcast', 'recipient_user'), name='broadcast_delivery_unique_recipient'),
                ),
            ],
        ),
    ]
