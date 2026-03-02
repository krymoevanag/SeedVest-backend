from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL


class Group(models.Model):
    INTERVAL_CHOICES = (
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('BIWEEKLY', 'Bi-Weekly'),
        ('MONTHLY', 'Monthly'),
    )

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    treasurer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='managed_groups'
    )
    savings_interval = models.CharField(
        max_length=20, 
        choices=INTERVAL_CHOICES, 
        default='MONTHLY'
    )
    is_penalty_enabled = models.BooleanField(default=True)
    penalty_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=100.00
    )
    min_saving_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=500.00
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Membership(models.Model):
    ROLE_CHOICES = (
        ('TREASURER', 'Treasurer'),
        ('MEMBER', 'Member'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_auto_penalty_enabled = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'group')

    def __str__(self):
        return f"{self.user} - {self.group} ({self.role})"
