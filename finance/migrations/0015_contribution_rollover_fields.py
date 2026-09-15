# Generated manually — adds rollover tracking fields to Contribution.
# is_rollover and rollover_source_cycle were added to the model in the
# Cycle Close Rollover Options feature but were missing a migration.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0014_loaninstallment"),
    ]

    operations = [
        migrations.AddField(
            model_name="contribution",
            name="is_rollover",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True when this contribution was automatically created by "
                    "a cycle-close rollover operation."
                ),
            ),
        ),
        migrations.AddField(
            model_name="contribution",
            name="rollover_source_cycle",
            field=models.ForeignKey(
                blank=True,
                help_text="The financial cycle whose unpaid balances generated this rollover contribution.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rollover_contributions",
                to="finance.financialcycle",
            ),
        ),
    ]
