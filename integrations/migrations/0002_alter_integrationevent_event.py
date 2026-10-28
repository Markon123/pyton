from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('integrations', '0001_initial')]

    operations = [
        migrations.AlterField(
            model_name='integrationevent',
            name='event',
            field=models.CharField(choices=[
                ('competition.published', 'competition.published'),
                ('competition.opened', 'competition.opened'),
                ('application.approved', 'application.approved'),
                ('protocol.published', 'protocol.published'),
            ], max_length=50),
        ),
    ]
