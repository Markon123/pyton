from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('training', '0002_alter_assessmentresult_direction_snapshot_and_more')]

    operations = [
        migrations.AddField(
            model_name='assessmentresult', name='norm_snapshot',
            field=models.JSONField(blank=True, default=dict, editable=False, verbose_name='Норматив и источник на дату записи'),
        ),
    ]
