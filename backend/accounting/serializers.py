from rest_framework import serializers

from .models import (Account, CostCentre, FiscalYear, JournalEntry, JournalLine, Payment,
                     PaymentAllocation, VendorBill)


class FiscalYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalYear
        fields = "__all__"


class AccountSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")
    current_balance = serializers.SerializerMethodField()
    is_group = serializers.BooleanField(read_only=True)

    class Meta:
        model = Account
        fields = "__all__"

    def get_current_balance(self, obj):
        return float(obj.balance())


class CostCentreSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    branch_name = serializers.CharField(source="branch.name", read_only=True, default="")

    class Meta:
        model = CostCentre
        fields = "__all__"


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")

    class Meta:
        model = JournalLine
        fields = ["id", "account", "account_code", "account_name", "debit", "credit",
                  "cost_centre", "cost_centre_name", "description"]


class JournalEntrySerializer(serializers.ModelSerializer):
    number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    lines = JournalLineSerializer(many=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default="")
    total_debit = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    total_credit = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    is_balanced = serializers.BooleanField(read_only=True)

    class Meta:
        model = JournalEntry
        fields = "__all__"
        read_only_fields = ["reversed_by"]

    def validate(self, attrs):
        lines = attrs.get("lines") or []
        if len(lines) < 2:
            raise serializers.ValidationError("A journal entry needs at least two lines.")
        debit = sum(line.get("debit") or 0 for line in lines)
        credit = sum(line.get("credit") or 0 for line in lines)
        if debit != credit:
            raise serializers.ValidationError(f"Entry does not balance: debits {debit} vs credits {credit}.")
        if not debit:
            raise serializers.ValidationError("A journal entry must carry a value.")
        for line in lines:
            if (line.get("debit") or 0) and (line.get("credit") or 0):
                raise serializers.ValidationError("Each line carries either a debit or a credit, not both.")
            account = line.get("account")
            if account is not None and account.is_group:
                raise serializers.ValidationError(
                    f"{account.code} {account.name} is a group heading. Post to one of its sub-accounts instead.")
        return attrs

    def create(self, validated_data):
        lines = validated_data.pop("lines")
        entry = JournalEntry.objects.create(**validated_data)
        for line in lines:
            JournalLine.objects.create(entry=entry, **line)
        return entry

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                JournalLine.objects.create(entry=instance, **line)
        return instance


class VendorBillSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    expense_account_name = serializers.CharField(source="expense_account.name", read_only=True, default="")
    balance_due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = VendorBill
        fields = "__all__"
        read_only_fields = ["journal_entry", "paid_amount"]


class PaymentAllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default="")
    bill_number = serializers.CharField(source="bill.number", read_only=True, default="")

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "invoice_number", "bill", "bill_number", "amount"]


class PaymentSerializer(serializers.ModelSerializer):
    number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    allocations = PaymentAllocationSerializer(many=True, required=False)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)
    unallocated_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Payment
        fields = "__all__"
        read_only_fields = ["journal_entry"]

    def validate(self, attrs):
        allocations = attrs.get("allocations") or []
        allocated = sum(item.get("amount") or 0 for item in allocations)
        amount = attrs.get("amount", getattr(self.instance, "amount", 0))
        if allocated > amount:
            raise serializers.ValidationError(f"Allocated {allocated} exceeds the payment amount {amount}.")
        for item in allocations:
            if bool(item.get("invoice")) == bool(item.get("bill")):
                raise serializers.ValidationError("Allocate each line against exactly one invoice or one bill.")
        return attrs

    def create(self, validated_data):
        allocations = validated_data.pop("allocations", [])
        payment = Payment.objects.create(**validated_data)
        for item in allocations:
            PaymentAllocation.objects.create(payment=payment, **item)
        return payment
