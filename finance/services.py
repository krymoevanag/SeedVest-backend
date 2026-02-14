from django.db.models import Sum, Count, Q
from django.utils import timezone
from .models import Contribution, Penalty

class InsightService:
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
