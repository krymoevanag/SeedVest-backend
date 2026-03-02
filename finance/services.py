from django.db import models, transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import date
import calendar
from .models import Contribution, Penalty, AutoSavingConfig, MonthlySavingGeneration
from notifications.models import Notification
from groups.models import Membership
from accounts.emails import send_penalty_notification_email

class InsightService:
    # ... (rest of InsightService stays same)
    def __init__(self, user):
        self.user = user

    def get_insights(self):
        """
        Main entry point to get all insights.
        """
        summary = self._get_summary()
        recommendations = self._generate_recommendations(summary)
        
        return {
            "summary": summary,
            "recommendations": recommendations,
            "generated_at": timezone.now()
        }

    def _get_summary(self):
        contributions = Contribution.objects.filter(user=self.user)
        penalties = Penalty.objects.filter(contribution__user=self.user)

        total_contributed = contributions.filter(status__in=["PAID", "LATE"]).aggregate(Sum("amount"))["amount__sum"] or 0
        total_penalties = penalties.aggregate(Sum("amount"))["amount__sum"] or 0
        
        # Punctuality metrics
        total_paid_count = contributions.filter(status__in=["PAID", "LATE"]).count()
        late_count = contributions.filter(status="LATE").count()
        on_time_percentage = 0
        if total_paid_count > 0:
            on_time_percentage = ((total_paid_count - late_count) / total_paid_count) * 100

        return {
            "total_contributed": total_contributed,
            "total_penalties_paid": total_penalties,
            "on_time_percentage": round(on_time_percentage, 1),
            "pending_contributions": contributions.filter(status="PENDING").count(),
            "overdue_contributions": contributions.filter(status="OVERDUE").count(),
        }

    def _generate_recommendations(self, summary):
        recommendations = []
        
        # 1. Penalty Analysis
        if summary["total_penalties_paid"] > 0:
            recommendations.append({
                "type": "WARNING",
                "message": f"You have paid a total of {summary['total_penalties_paid']} in penalties. Setting up calendar reminders can help save money.",
                "action": "Set Reminder"
            })
        
        # 2. Punctuality Analysis
        if summary["on_time_percentage"] < 80 and summary["total_contributed"] > 0:
             recommendations.append({
                "type": "TIP",
                "message": "Your on-time payment score is below 80%. Try paying 2 days before the due date to account for processing delays.",
                "action": "View Due Dates"
            })
        elif summary["on_time_percentage"] == 100 and summary["total_contributed"] > 0:
             recommendations.append({
                "type": "SUCCESS",
                "message": "Excellent! You have a perfect payment record. Keep it up to build trust within your group.",
                "action": None
            })

        # 3. Urgent Actions
        if summary["overdue_contributions"] > 0:
             recommendations.append({
                "type": "URGENT",
                "message": f"You have {summary['overdue_contributions']} overdue payments. Please clear them immediately to avoid further penalties.",
                "action": "Pay Now"
            })

        return recommendations


class AutoSaveService:
    @staticmethod
    def generate_contributions(dry_run=False):
        """
        Runs at the start of a period (e.g., daily) to create PENDING 
        contributions for members based on their AutoSavingConfig.
        """
        today = timezone.now().date()
        active_configs = AutoSavingConfig.objects.filter(is_active=True).select_related('group', 'user')
        
        created_count = 0
        skipped_count = 0
        errors = []

        for config in active_configs:
            group = config.group
            interval = group.savings_interval
            
            is_due_today = False
            if interval == 'DAILY':
                is_due_today = True
            elif interval == 'WEEKLY':
                is_due_today = (today.weekday() + 1) == (config.day_of_month % 7 or 7)
            elif interval == 'BIWEEKLY':
                is_due_today = (today.timetuple().tm_yday % 14) == (config.day_of_month % 14)
            elif interval == 'MONTHLY':
                is_due_today = today.day == config.day_of_month

            if is_due_today:
                # Check if a requirement already exists for this interval window
                # For simplicity, we check if a contribution was created today
                exists = Contribution.objects.filter(
                    user=config.user,
                    group=group,
                    created_at__date=today,
                ).exists()

                if not exists:
                    if not dry_run:
                        try:
                            with transaction.atomic():
                                # Set due date to end of interval (simplified)
                                if interval == 'DAILY':
                                    due_date = today
                                elif interval == 'WEEKLY':
                                    due_date = today + timezone.timedelta(days=(6 - today.weekday()))
                                else:
                                    last_day = calendar.monthrange(today.year, today.month)[1]
                                    due_date = date(today.year, today.month, last_day)

                                Contribution.objects.create(
                                    user=config.user,
                                    group=group,
                                    amount=config.amount, # Member's specific target
                                    due_date=due_date,
                                    status="PENDING",
                                )
                                created_count += 1
                        except Exception as e:
                            errors.append(f"Creation error {config.user.email}: {e}")
                    else:
                        created_count += 1
                else:
                    skipped_count += 1

        return created_count, skipped_count, errors

    @staticmethod
    def enforce_savings_compliance(dry_run=False, force=False):
        """
        Runs to check if members met the minimum saving threshold for the period.
        If not, issues penalties and sends email notifications.
        Should be run at interval boundaries (e.g., 1st of month, Monday morning).
        Set force=True for manual overrides to check compliance for current/prev window.
        """
        today = timezone.now().date()
        groups = Group.objects.all()
        
        penalty_count = 0
        errors = []

        for group in groups:
            interval = group.savings_interval
            
            # Determine if we should check compliance today
            should_check = False
            start_check = None
            end_check = None

            if interval == 'DAILY':
                # Check yesterday's compliance
                should_check = True
                end_check = today - timezone.timedelta(days=1)
                start_check = end_check
            elif interval == 'WEEKLY' and today.weekday() == 0: # Monday
                should_check = True
                end_check = today - timezone.timedelta(days=1) # Sunday
                start_check = today - timezone.timedelta(days=7) # Preceding Monday
            elif interval == 'MONTHLY' and today.day == 1: # 1st of month
                should_check = True
                # Last month
                last_month_date = today - timezone.timedelta(days=1)
                start_check = date(last_month_date.year, last_month_date.month, 1)
                end_check = last_month_date

            if not should_check and not force:
                continue

            if force and not should_check:
                # If forced, calculate periods based on 'today' even if not boundary
                if interval == 'DAILY':
                    end_check = today - timezone.timedelta(days=1)
                    start_check = end_check
                elif interval == 'WEEKLY':
                    # Check the last full week (Mon-Sun)
                    days_since_monday = today.weekday()
                    end_check = today - timezone.timedelta(days=days_since_monday + 1)
                    start_check = end_check - timezone.timedelta(days=6)
                elif interval == 'MONTHLY':
                    # Check last calendar month
                    first_day_this_month = today.replace(day=1)
                    end_check = first_day_this_month - timezone.timedelta(days=1)
                    start_check = end_check.replace(day=1)

            # Check all memberships in this group
            memberships = Membership.objects.filter(group=group).select_related('user')
            for membership in memberships:
                user = membership.user
                
                # Sum PAID contributions in the period
                total_saved = Contribution.objects.filter(
                    user=user,
                    group=group,
                    status__in=['PAID', 'LATE'],
                    created_at__date__gte=start_check,
                    created_at__date__lte=end_check
                ).aggregate(total=Sum('amount'))['total'] or 0

                if total_saved < group.min_saving_amount:
                    # Issue penalty if enabled
                    if group.is_penalty_enabled and membership.is_auto_penalty_enabled:
                        if not dry_run:
                            try:
                                with transaction.atomic():
                                    reason = f"Missed minimum saving requirement for {interval} period ({start_check} to {end_check}). Required: {group.min_saving_amount}, Saved: {total_saved}"
                                    
                                    # Create penalty (standalone or linked to an overdue contribution if any)
                                    # We'll create it without a specific contribution link if none exists
                                    Penalty.objects.create(
                                        user=user,
                                        amount=group.penalty_amount,
                                        reason=reason,
                                    )
                                    
                                    # Send email
                                    send_penalty_notification_email(
                                        user, 
                                        group.penalty_amount, 
                                        group.name, 
                                        reason
                                    )
                                    
                                    # Also create in-app notification
                                    Notification.objects.create(
                                        recipient=user,
                                        type="ERROR",
                                        title=f"Penalty Issued - {group.name}",
                                        message=reason
                                    )
                                    penalty_count += 1
                            except Exception as e:
                                errors.append(f"Penalty error {user.email}: {e}")
                        else:
                            penalty_count += 1

        return penalty_count, errors
