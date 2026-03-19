# Создано Django 6.0.1 2026-03-11 14:22

import django.db.models.deletion
import django.db.models.functions.datetime
import videofetch_app.models
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AnalyseSlot',
            fields=[
                ('slot_id', models.BigAutoField(db_column='slot_id', primary_key=True, serialize=False)),
                ('holder', models.TextField(choices=[('hold', 'Hold'), ('free', 'Free')], default='free')),
                ('lease_until', models.DateTimeField(null=True)),
            ],
            options={
                'db_table': 'analysis_slots',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='Job',
            fields=[
                ('id', models.BigAutoField(db_column='id', primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('updated_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('created_via', models.TextField(choices=[('telegram', 'Telegram'), ('web', 'Web')], default='web')),
                ('telegram_user_id', models.BigIntegerField(null=True)),
                ('telegram_chat_id', models.BigIntegerField(null=True)),
                ('progress_msg_id', models.BigIntegerField(null=True)),
                ('source_url', models.TextField()),
                ('title', models.TextField(null=True)),
                ('duration_seconds', models.IntegerField(null=True)),
                ('is_short', models.BooleanField(db_default=False)),
                ('requested_quality', models.TextField(null=True)),
                ('requested_audio', models.TextField(null=True)),
                ('selected_format', models.JSONField(null=True)),
                ('selected_audio', models.JSONField(null=True)),
                ('status', models.TextField(choices=[('queued', 'Queued'), ('running', 'Running'), ('done', 'Done'), ('failed', 'Failed'), ('canceled', 'Canceled')], default='queued')),
                ('priority', models.IntegerField(db_default=0)),
                ('progress', models.IntegerField(db_default=0, validators=[videofetch_app.models.progress_validator])),
                ('stage', models.TextField(null=True)),
                ('attempts', models.IntegerField(db_default=0)),
                ('run_after', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('locked_by', models.TextField(null=True)),
                ('locked_at', models.DateTimeField(null=True)),
                ('result_path', models.TextField(null=True)),
                ('result_size_bytes', models.BigIntegerField(null=True)),
                ('result_meta', models.JSONField(null=True)),
                ('error_code', models.TextField(null=True)),
                ('error_message', models.TextField(null=True)),
            ],
            options={
                'db_table': 'download_jobs',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(db_column='id', primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('marketing_opt_in', models.BooleanField(db_default=False)),
            ],
            options={
                'db_table': 'users',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='Message',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('formats', models.TextField()),
                ('video_info', models.TextField()),
                ('url', models.TextField()),
                ('duration_text', models.TextField()),
                ('is_movie', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='videofetch_app.user')),
            ],
        ),
    ]
