from django.db import models

class Timestamped(models.Model):
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True

class Customer(Timestamped):
    name=models.CharField(max_length=180); gstin=models.CharField(max_length=15,unique=True); pan=models.CharField(max_length=10,blank=True)
    phone=models.CharField(max_length=20,blank=True); email=models.EmailField(blank=True); billing_address=models.TextField(blank=True)
    credit_limit=models.DecimalField(max_digits=14,decimal_places=2,default=0); kyc_status=models.CharField(max_length=20,default="pending")
    def __str__(self): return self.name

class Driver(Timestamped):
    name=models.CharField(max_length=120); phone=models.CharField(max_length=20,unique=True); licence_number=models.CharField(max_length=40,unique=True)
    licence_expiry=models.DateField(null=True,blank=True); status=models.CharField(max_length=20,default="available")
    current_latitude=models.DecimalField(max_digits=9,decimal_places=6,null=True,blank=True); current_longitude=models.DecimalField(max_digits=9,decimal_places=6,null=True,blank=True)
    def __str__(self): return self.name

class Vehicle(Timestamped):
    registration_number=models.CharField(max_length=20,unique=True); vehicle_type=models.CharField(max_length=60); capacity_kg=models.PositiveIntegerField(default=0)
    ownership=models.CharField(max_length=20,default="owned"); status=models.CharField(max_length=20,default="available"); gps_device_id=models.CharField(max_length=100,blank=True)
    insurance_expiry=models.DateField(null=True,blank=True); permit_expiry=models.DateField(null=True,blank=True)
    def __str__(self): return self.registration_number

class LorryReceipt(Timestamped):
    number=models.CharField(max_length=30,unique=True); customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name="lorry_receipts")
    consignor=models.CharField(max_length=180); consignee=models.CharField(max_length=180); origin=models.CharField(max_length=120); destination=models.CharField(max_length=120)
    material=models.CharField(max_length=180); weight_kg=models.DecimalField(max_digits=12,decimal_places=2); packages=models.PositiveIntegerField(default=1)
    eway_bill_number=models.CharField(max_length=30,blank=True); freight_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); status=models.CharField(max_length=20,default="booked")
    def __str__(self): return self.number

class Trip(Timestamped):
    number=models.CharField(max_length=30,unique=True); vehicle=models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name="trips")
    driver=models.ForeignKey(Driver,on_delete=models.PROTECT,related_name="trips"); lorry_receipts=models.ManyToManyField(LorryReceipt,related_name="trips")
    origin=models.CharField(max_length=120); destination=models.CharField(max_length=120); planned_departure=models.DateTimeField()
    actual_departure=models.DateTimeField(null=True,blank=True); arrival_at=models.DateTimeField(null=True,blank=True)
    advance_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); estimated_cost=models.DecimalField(max_digits=12,decimal_places=2,default=0); status=models.CharField(max_length=20,default="planned")
    def __str__(self): return self.number

class TrackingEvent(Timestamped):
    trip=models.ForeignKey(Trip,on_delete=models.CASCADE,related_name="tracking_events"); event_type=models.CharField(max_length=40,default="position")
    latitude=models.DecimalField(max_digits=9,decimal_places=6); longitude=models.DecimalField(max_digits=9,decimal_places=6); speed_kph=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    recorded_at=models.DateTimeField(); description=models.CharField(max_length=240,blank=True)

class Invoice(Timestamped):
    number=models.CharField(max_length=30,unique=True); customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name="invoices")
    trip=models.ForeignKey(Trip,on_delete=models.PROTECT,related_name="invoices"); freight_amount=models.DecimalField(max_digits=12,decimal_places=2)
    additional_charges=models.DecimalField(max_digits=12,decimal_places=2,default=0); tax_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    total_amount=models.DecimalField(max_digits=12,decimal_places=2); due_date=models.DateField(); status=models.CharField(max_length=20,default="draft")
    def __str__(self): return self.number

class Settlement(Timestamped):
    trip=models.ForeignKey(Trip,on_delete=models.PROTECT,related_name="settlements"); driver=models.ForeignKey(Driver,on_delete=models.PROTECT,related_name="settlements")
    advance_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); approved_expenses=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    net_payable=models.DecimalField(max_digits=12,decimal_places=2,default=0); status=models.CharField(max_length=20,default="pending")

class SalesQuote(Timestamped):
    number=models.CharField(max_length=30,unique=True); customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name="quotes")
    origin=models.CharField(max_length=120); destination=models.CharField(max_length=120); freight_amount=models.DecimalField(max_digits=12,decimal_places=2)
    valid_until=models.DateField(); status=models.CharField(max_length=20,default="draft")
    def __str__(self): return self.number
