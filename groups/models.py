from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL


class Group(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    treasurer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='managed_groups'
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
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'group')

    def __str__(self):
        return f"{self.user} - {self.group} ({self.role})"
