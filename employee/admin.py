from django.contrib import admin
from .models import Employee, EmployeeLedger, EmployeeAttendance, EmployeeDocument


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'pg', 'salary', 'joining_date', 'is_active']
    list_filter = ['is_active', 'pg', 'joining_date']
    search_fields = ['name', 'phone']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Personal Information', {
            'fields': ('name', 'phone', 'emergency_contact', 'selfie', 'aadhaar')
        }),
        ('Employment Details', {
            'fields': ('pg', 'salary', 'joining_date', 'salary_date', 'work_notes', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(EmployeeLedger)
class EmployeeLedgerAdmin(admin.ModelAdmin):
    list_display = ['employee', 'transaction_type', 'amount', 'date', 'created_by', 'created_at']
    list_filter = ['transaction_type', 'date', 'employee__pg']
    search_fields = ['employee__name', 'description']
    readonly_fields = ['created_at', 'created_by']
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(EmployeeAttendance)
class EmployeeAttendanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'status', 'check_in_time', 'check_out_time', 'marked_by']
    list_filter = ['status', 'date', 'employee__pg']
    search_fields = ['employee__name']
    readonly_fields = ['created_at', 'updated_at', 'marked_by']
    date_hierarchy = 'date'
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.marked_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ['employee', 'document_type', 'document_number', 'issue_date', 'expiry_date', 'is_expired']
    list_filter = ['document_type', 'employee__pg']
    search_fields = ['employee__name', 'document_number']
    readonly_fields = ['created_at', 'updated_at', 'uploaded_by']
    
    def is_expired(self, obj):
        return obj.is_expired()
    is_expired.boolean = True
    is_expired.short_description = 'Expired'
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

