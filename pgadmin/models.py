from django.db import models
from django.contrib.auth import get_user_model
from core.models import TimeStampedModel
from django.utils.text import slugify
from django.db.models import Q


class PG(TimeStampedModel):
	name = models.CharField(max_length=200)
	slug = models.SlugField(max_length=200, unique=True, blank=True)
	address = models.TextField()
	# Optional contact phone number for the PG (10-15 digits to allow country codes)
	phone = models.CharField(max_length=20, blank=True, help_text="Contact phone number")
	created_by_admin = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='created_pgs')
	referral_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Default referral credit amount for this PG.")
	# Allow PG admins to permit selecting past joining dates in quick booking flows
	past_joining_date_allowed = models.BooleanField(default=False, help_text="Allow selecting a joining date in the past for quick bookings.")
	# Allow tenants to choose a custom leaving date when submitting a leave request
	allow_custom_leave_date = models.BooleanField(default=True, blank=True, help_text="Allow tenants to select a custom leaving date when requesting to leave the PG.")
	# Notice period in days (default 30 days)
	notice_period = models.PositiveIntegerField(default=30, help_text="Notice period in days required before leaving.")
	# Day-wise booking fee per day
	daywise_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Fee per day for day-wise/short-term bookings.")
	# WhatsApp group invite settings
	whatsapp_invite_link = models.URLField(max_length=500, blank=True, null=True, help_text="WhatsApp group invite link for this PG.")
	whatsapp_invite_message = models.TextField(blank=True, null=True, help_text="Custom message to send with the WhatsApp group invite link.")

	def __str__(self):
		return self.name

	def save(self, *args, **kwargs):
		# Auto-generate a unique slug from name if missing or if name changed with empty slug
		if not self.slug and self.name:
			base = slugify(self.name) or 'pg'
			slug = base
			# Ensure uniqueness
			counter = 2
			Model = type(self)
			while Model.objects.filter(slug=slug).exclude(pk=self.pk).exists():
				slug = f"{base}-{counter}"
				counter += 1
			self.slug = slug
		super().save(*args, **kwargs)


class PGAdmin(TimeStampedModel):
	# Allow a single user to manage multiple PGs
	user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='pg_admin_profile')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='admins')

	def __str__(self):
		return f"{self.user} ({self.pg})"


class WhatsAppCloudConfig(TimeStampedModel):
	"""Opt-in Cloud API configuration. Disabled means all legacy behaviour is retained."""
	pg = models.OneToOneField(PG, on_delete=models.CASCADE, related_name='whatsapp_cloud_config')
	enabled = models.BooleanField(default=False)
	api_base_url = models.URLField(max_length=500, default='https://graph.facebook.com')
	api_version = models.CharField(max_length=20, default='v25.0')
	messages_endpoint = models.CharField(
		max_length=700, blank=True,
		help_text='Optional full endpoint. Use {phone_number_id} as a placeholder, or leave blank to derive it.'
	)
	phone_number_id = models.CharField(max_length=100, blank=True, null=True)
	business_account_id = models.CharField(max_length=100, blank=True)
	display_phone_number = models.CharField(max_length=30, blank=True)
	template_language = models.CharField(max_length=20, default='en_US')
	monthly_template_name = models.CharField(max_length=255, blank=True)
	messages_template_name = models.CharField(max_length=255, blank=True)
	leaving_template_name = models.CharField(max_length=255, blank=True)
	compliance_template_name = models.CharField(max_length=255, blank=True)
	access_token_encrypted = models.TextField(blank=True)
	verify_token_encrypted = models.TextField(blank=True)
	app_secret_encrypted = models.TextField(blank=True)
	enable_monthly_dashboard = models.BooleanField(default=False)
	enable_whatsapp_messages = models.BooleanField(default=False)
	enable_leaving_page = models.BooleanField(default=False)
	enable_compliance_page = models.BooleanField(default=False)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=['phone_number_id'],
				condition=Q(phone_number_id__isnull=False) & ~Q(phone_number_id=''),
				name='unique_whatsapp_cloud_phone_number_id',
			),
		]

	def __str__(self):
		return f"Cloud API for {self.pg}"

	def section_enabled(self, section):
		field = {
			'monthly_dashboard': 'enable_monthly_dashboard',
			'whatsapp_messages': 'enable_whatsapp_messages',
			'leaving_page': 'enable_leaving_page',
			'compliance_page': 'enable_compliance_page',
		}.get(section)
		return bool(self.enabled and field and getattr(self, field, False))

	def template_for_section(self, section):
		return {
			'monthly_dashboard': self.monthly_template_name,
			'whatsapp_messages': self.messages_template_name,
			'leaving_page': self.leaving_template_name,
			'compliance_page': self.compliance_template_name,
		}.get(section, '')

	@property
	def resolved_messages_endpoint(self):
		if self.messages_endpoint:
			return self.messages_endpoint.replace('{phone_number_id}', self.phone_number_id or '')
		return f"{self.api_base_url.rstrip('/')}/{self.api_version.strip('/')}/{self.phone_number_id}/messages"


class WhatsAppContact(TimeStampedModel):
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='whatsapp_contacts')
	wa_id = models.CharField(max_length=30)
	name = models.CharField(max_length=200, blank=True)
	user = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='whatsapp_contacts')

	class Meta:
		constraints = [models.UniqueConstraint(fields=['pg', 'wa_id'], name='unique_whatsapp_contact_per_pg')]
		indexes = [models.Index(fields=['pg', 'wa_id'])]

	def __str__(self):
		return self.name or self.wa_id


class WhatsAppConversation(TimeStampedModel):
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='whatsapp_conversations')
	contact = models.ForeignKey(WhatsAppContact, on_delete=models.CASCADE, related_name='conversations')
	last_message_at = models.DateTimeField(null=True, blank=True)
	unread_count = models.PositiveIntegerField(default=0)

	class Meta:
		constraints = [models.UniqueConstraint(fields=['pg', 'contact'], name='unique_whatsapp_conversation_per_pg')]
		ordering = ['-last_message_at', '-updated_at']


class WhatsAppMessage(TimeStampedModel):
	INBOUND = 'inbound'
	OUTBOUND = 'outbound'
	DIRECTION_CHOICES = [(INBOUND, 'Inbound'), (OUTBOUND, 'Outbound')]
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='whatsapp_messages')
	conversation = models.ForeignKey(WhatsAppConversation, on_delete=models.CASCADE, related_name='messages')
	provider_message_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
	direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
	message_type = models.CharField(max_length=30, default='text')
	text = models.TextField(blank=True)
	media_id = models.CharField(max_length=255, blank=True)
	media_url = models.URLField(max_length=1000, blank=True)
	status = models.CharField(max_length=30, default='received')
	section = models.CharField(max_length=40, blank=True)
	provider_timestamp = models.DateTimeField(null=True, blank=True)
	sent_by = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_whatsapp_messages')
	error_message = models.TextField(blank=True)
	raw_payload = models.JSONField(default=dict, blank=True)

	class Meta:
		ordering = ['provider_timestamp', 'created_at']
		indexes = [models.Index(fields=['pg', 'conversation', 'created_at']), models.Index(fields=['pg', 'status'])]


class WhatsAppWebhookEvent(TimeStampedModel):
	payload_hash = models.CharField(max_length=64, unique=True)
	phone_number_id = models.CharField(max_length=100, blank=True)
	pg = models.ForeignKey(PG, on_delete=models.SET_NULL, null=True, blank=True, related_name='whatsapp_webhook_events')
	processed = models.BooleanField(default=False)
	payload = models.JSONField(default=dict)
	error_message = models.TextField(blank=True)


class PGAdminPermission(TimeStampedModel):
	"""
	Permissions model for PG Admins. Controls access to specific features.
	All permissions default to False.
	"""
	pg_admin = models.OneToOneField(PGAdmin, on_delete=models.CASCADE, related_name='permissions')
	
	# Employee Management
	can_view_employees = models.BooleanField(default=False, help_text='Can view employee details for their PG')
	can_edit_employees = models.BooleanField(default=False, help_text='Can create, edit and delete employees for their PG')
	
	# Payment Management
	can_delete_payments = models.BooleanField(default=False, help_text='Can delete payment entries')
	can_edit_payments = models.BooleanField(default=False, help_text='Can edit payment entries')
	
	# Application Management
	can_edit_applications = models.BooleanField(default=False, help_text='Can edit tenant applications for their PG')
	
	# Booking Management
	can_delete_confirmed_bookings = models.BooleanField(default=False, help_text='Can delete confirmed/approved bookings')
	
	class Meta:
		verbose_name = 'PG Admin Permission'
		verbose_name_plural = 'PG Admin Permissions'
	
	def __str__(self):
		return f"Permissions for {self.pg_admin}"
	
	@classmethod
	def get_or_create_for_admin(cls, pg_admin):
		"""Get or create permission record for a PG Admin"""
		perm, created = cls.objects.get_or_create(pg_admin=pg_admin)
		return perm


class Complaint(TimeStampedModel):
	"""
	Model for tenant complaints. Only users with active bookings can create complaints.
	"""
	# Status choices
	OPEN = 'open'
	IN_PROGRESS = 'in_progress'
	SOLVED = 'solved'
	
	STATUS_CHOICES = [
		(OPEN, 'Open'),
		(IN_PROGRESS, 'In Progress'),
		(SOLVED, 'Solved'),
	]
	
	# Priority choices
	LOW = 'low'
	MEDIUM = 'medium'
	HIGH = 'high'
	URGENT = 'urgent'
	
	PRIORITY_CHOICES = [
		(LOW, 'Low'),
		(MEDIUM, 'Medium'),
		(HIGH, 'High'),
		(URGENT, 'Urgent'),
	]
	
	# Category choices
	MAINTENANCE = 'maintenance'
	CLEANLINESS = 'cleanliness'
	FOOD = 'food'
	WIFI = 'wifi'
	ELECTRICITY = 'electricity'
	WATER = 'water'
	SECURITY = 'security'
	NOISE = 'noise'
	OTHER = 'other'
	
	CATEGORY_CHOICES = [
		(MAINTENANCE, 'Maintenance'),
		(CLEANLINESS, 'Cleanliness'),
		(FOOD, 'Food'),
		(WIFI, 'WiFi/Internet'),
		(ELECTRICITY, 'Electricity'),
		(WATER, 'Water Supply'),
		(SECURITY, 'Security'),
		(NOISE, 'Noise'),
		(OTHER, 'Other'),
	]
	
	# Foreign keys
	user = models.ForeignKey(
		get_user_model(), 
		on_delete=models.CASCADE, 
		related_name='complaints',
		help_text='User who raised the complaint'
	)
	pg = models.ForeignKey(
		PG, 
		on_delete=models.CASCADE, 
		related_name='complaints',
		help_text='PG where the complaint was raised'
	)
	booking = models.ForeignKey(
		'bookings.Booking', 
		on_delete=models.SET_NULL, 
		null=True, 
		blank=True,
		related_name='complaints',
		help_text='Active booking reference'
	)
	
	# Complaint details
	title = models.CharField(max_length=200, help_text='Brief title of the complaint')
	description = models.TextField(help_text='Detailed description of the issue')
	category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=OTHER)
	priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=MEDIUM)
	status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=OPEN)
	
	# Tracking
	resolved_at = models.DateTimeField(null=True, blank=True, help_text='When the complaint was marked as solved')
	resolved_by = models.ForeignKey(
		get_user_model(), 
		on_delete=models.SET_NULL, 
		null=True, 
		blank=True,
		related_name='resolved_complaints',
		help_text='Admin who resolved the complaint'
	)
	
	class Meta:
		ordering = ['-created_at']
		indexes = [
			models.Index(fields=['pg', 'status']),
			models.Index(fields=['user', 'status']),
			models.Index(fields=['created_at']),
		]
	
	def __str__(self):
		return f"{self.title} - {self.user.get_full_name() or self.user.email} ({self.status})"
	
	def get_status_badge_class(self):
		"""Return Bootstrap badge class for status"""
		return {
			self.OPEN: 'bg-danger',
			self.IN_PROGRESS: 'bg-warning text-dark',
			self.SOLVED: 'bg-success',
		}.get(self.status, 'bg-secondary')
	
	def get_priority_badge_class(self):
		"""Return Bootstrap badge class for priority"""
		return {
			self.LOW: 'bg-info',
			self.MEDIUM: 'bg-primary',
			self.HIGH: 'bg-warning text-dark',
			self.URGENT: 'bg-danger',
		}.get(self.priority, 'bg-secondary')
	
	def get_category_icon(self):
		"""Return icon class for category"""
		return {
			self.MAINTENANCE: 'bi-tools',
			self.CLEANLINESS: 'bi-stars',
			self.WIFI: 'bi-wifi',
			self.ELECTRICITY: 'bi-lightning-charge',
			self.WATER: 'bi-droplet',
			self.SECURITY: 'bi-shield-check',
			self.NOISE: 'bi-volume-up',
			self.OTHER: 'bi-question-circle',
		}.get(self.category, 'bi-chat-dots')


class ComplaintMedia(TimeStampedModel):
	"""
	Media files (images/videos) attached to complaints.
	Uploaded to Google Drive and URL stored here.
	"""
	MEDIA_TYPE_IMAGE = 'image'
	MEDIA_TYPE_VIDEO = 'video'
	MEDIA_TYPE_CHOICES = [
		(MEDIA_TYPE_IMAGE, 'Image'),
		(MEDIA_TYPE_VIDEO, 'Video'),
	]
	
	complaint = models.ForeignKey(
		Complaint,
		on_delete=models.CASCADE,
		related_name='media_files'
	)
	media_type = models.CharField(max_length=10, choices=MEDIA_TYPE_CHOICES, default=MEDIA_TYPE_IMAGE)
	file_url = models.URLField(max_length=500, help_text='Google Drive URL of the uploaded file')
	file_name = models.CharField(max_length=255, blank=True, help_text='Original filename')
	file_size = models.BigIntegerField(null=True, blank=True, help_text='File size in bytes')
	thumbnail_url = models.URLField(max_length=500, blank=True, help_text='Thumbnail URL for images/videos')
	
	class Meta:
		ordering = ['created_at']
		verbose_name = 'Complaint Media'
		verbose_name_plural = 'Complaint Media'
	
	def __str__(self):
		return f"{self.media_type} for complaint #{self.complaint_id}"
	
	def is_image(self):
		return self.media_type == self.MEDIA_TYPE_IMAGE
	
	def is_video(self):
		return self.media_type == self.MEDIA_TYPE_VIDEO


class ComplaintComment(TimeStampedModel):
	"""
	Comments/updates on complaints by PG admins
	"""
	complaint = models.ForeignKey(
		Complaint, 
		on_delete=models.CASCADE, 
		related_name='comments'
	)
	user = models.ForeignKey(
		get_user_model(), 
		on_delete=models.CASCADE,
		help_text='Admin who added the comment'
	)
	comment = models.TextField(help_text='Comment or update on the complaint')
	is_internal = models.BooleanField(
		default=False, 
		help_text='Internal notes (not visible to tenant)'
	)
	
	class Meta:
		ordering = ['created_at']
	
	def __str__(self):
		return f"Comment on {self.complaint.title} by {self.user.get_full_name() or self.user.email}"


class OldTenant(TimeStampedModel):
	"""
	Archive of tenant data from deleted bookings.
	Stores key personal information before booking deletion for historical records.
	"""
	pg = models.ForeignKey(
		PG, 
		on_delete=models.CASCADE, 
		related_name='old_tenants',
		help_text='PG where the tenant resided'
	)
	
	# Personal details
	full_name = models.CharField(max_length=255, help_text='Full name of the tenant')
	dob = models.DateField(null=True, blank=True)
	age = models.PositiveSmallIntegerField(null=True, blank=True)
	father_name = models.CharField(max_length=255, blank=True, help_text="Father's name")
	father_phone = models.CharField(max_length=20, blank=True)
	mother_name = models.CharField(max_length=255, blank=True, help_text="Mother's name")
	mother_phone = models.CharField(max_length=20, blank=True)
	
	# Contact details
	email = models.EmailField(help_text='Email address')
	phone = models.CharField(max_length=20, blank=True, help_text='Phone number')
	emergency_contact = models.CharField(max_length=20, blank=True, help_text="Emergency contact number")
	whatsapp_number = models.CharField(max_length=20, blank=True, help_text='WhatsApp number')
	
	# Address & Demographics
	address = models.TextField(blank=True, help_text='Permanent address')
	food_pref = models.CharField(max_length=10, choices=[('veg','Veg'),('nonveg','Non-Veg')], blank=True)
	marital_status = models.CharField(max_length=10, choices=[('single','Single'),('married','Married')], blank=True)
	education = models.CharField(max_length=255, blank=True)
	occupation = models.CharField(max_length=20, choices=[('student','Student'),('employee','Employee')], blank=True)
	org_name = models.CharField(max_length=255, blank=True)
	org_address = models.TextField(blank=True)
	
	# Stay details
	room_no = models.CharField(max_length=20, blank=True, help_text='Room number during stay')
	bed_no = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Bed/share number during stay')
	joining_date = models.DateField(null=True, blank=True, help_text='Date when tenant joined')
	leaving_date = models.DateField(null=True, blank=True, help_text='Date when tenant left')
	leaving_reason = models.TextField(blank=True, help_text='Reason for leaving')
	
	# Documents & Media
	aadhaar_number = models.CharField(max_length=20, blank=True)
	selfie_url = models.URLField(blank=True)
	aadhaar_file_url = models.URLField(blank=True)
	aadhaar_file_url_2 = models.URLField(blank=True)
	
	# Vehicle details
	has_vehicle = models.BooleanField(default=False)
	vehicle_number = models.CharField(max_length=32, blank=True)
	vehicle_model = models.CharField(max_length=100, blank=True)
	
	# Financial info
	advance_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Advance amount paid')
	advance_returned = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Advance amount returned')
	
	# Reference to original user (nullable - user might be deleted)
	original_user = models.ForeignKey(
		get_user_model(),
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='old_tenant_records',
		help_text='Original user account (if still exists)'
	)
	
	# Original booking ID for reference
	original_booking_id = models.IntegerField(null=True, blank=True, help_text='Original booking ID before deletion')
	
	# Archive metadata
	archived_at = models.DateTimeField(auto_now_add=True, help_text='When this record was archived')
	archived_by = models.ForeignKey(
		get_user_model(),
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='archived_tenants',
		help_text='Admin who archived/deleted the booking'
	)
	
	class Meta:
		ordering = ['-archived_at']
		indexes = [
			models.Index(fields=['pg', 'archived_at']),
			models.Index(fields=['joining_date']),
			models.Index(fields=['leaving_date']),
			models.Index(fields=['full_name']),
		]
	
	def __str__(self):
		return f"{self.full_name} - {self.pg.name} ({self.leaving_date})"
	
	@property
	def stay_duration_days(self):
		"""Calculate the duration of stay in days"""
		if self.joining_date and self.leaving_date:
			return (self.leaving_date - self.joining_date).days
		return None
