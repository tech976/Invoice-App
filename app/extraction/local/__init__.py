"""Reading bills without a model.

Every incoming bill is a computer-generated PDF, so the characters are already
in the file — there is nothing to infer. This package reads them off the text
layer by position, confirms them against the e-invoice QR, and hands the
result to the same validation rules a model's reading would face.

See EXTRACTION_PLAN.md for the design and the build order.
"""
