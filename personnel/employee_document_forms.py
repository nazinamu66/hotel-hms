from pathlib import Path

from django import forms

from .models import EmployeeDocument


MAX_EMPLOYEE_DOCUMENT_SIZE = 10 * 1024 * 1024
ALLOWED_EMPLOYEE_DOCUMENT_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx",
}


class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ["title", "category", "description", "file"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")
        if not uploaded:
            return uploaded

        extension = Path(uploaded.name).suffix.lower()
        if extension not in ALLOWED_EMPLOYEE_DOCUMENT_EXTENSIONS:
            raise forms.ValidationError(
                "Upload a PDF, JPG, PNG, DOC or DOCX document."
            )
        if uploaded.size > MAX_EMPLOYEE_DOCUMENT_SIZE:
            raise forms.ValidationError("The document must not exceed 10 MB.")

        # Check signatures for common binary formats; browser-provided MIME
        # types are not trusted. Office formats are ZIP containers.
        head = uploaded.read(8)
        uploaded.seek(0)
        if extension == ".pdf" and not head.startswith(b"%PDF-"):
            raise forms.ValidationError("The uploaded file is not a valid PDF.")
        if extension in {".jpg", ".jpeg"} and not head.startswith(b"\\xff\\xd8\\xff"):
            raise forms.ValidationError("The uploaded file is not a valid JPEG image.")
        if extension == ".png" and not head.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
            raise forms.ValidationError("The uploaded file is not a valid PNG image.")
        if extension in {".docx"} and not head.startswith(b"PK"):
            raise forms.ValidationError("The uploaded file is not a valid DOCX document.")
        return uploaded
