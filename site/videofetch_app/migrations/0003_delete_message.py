from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('videofetch_app', '0002_broadcasts'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='Message'),
            ],
        ),
    ]
