from rest_framework import serializers

class MemberAnalyticsSerializer(serializers.Serializer):
    core_metrics = serializers.DictField()
    distributions = serializers.DictField()
    trends = serializers.DictField()
    lifecycle = serializers.DictField()

class GroupAnalyticsSerializer(serializers.Serializer):
    group_metrics = serializers.DictField()
    distributions = serializers.DictField()
    trends = serializers.DictField()
