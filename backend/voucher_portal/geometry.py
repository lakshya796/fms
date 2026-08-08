"""Default field layout for the ADCOOP coupon template, measured from the
approved DiscountCoupon.pdf content stream. All positions are points from the
coupon's top-left corner, y growing downward."""

DEFAULT_FIELD_GEOMETRY = {
    "artwork": {"x": 2.4, "y": 2.5, "w": 476.8, "h": 174},
    "card": {"x": 2, "y": 5, "w": 144, "h": 164, "fill": "#FFFFFF"},
    "fields": [
        {"key": "discount_numeral", "x": 35, "y": 20, "size": 30, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "discount_unit", "x": 86, "y": 15, "size": 14, "font": "Helvetica", "color": "#4A4160"},
        {"key": "off_label", "x": 84.5, "y": 36, "size": 14, "font": "Helvetica", "color": "#4A4160"},
        {"key": "qualifier", "x": 36.5, "y": 58, "size": 6.5, "font": "Helvetica", "color": "#6B6480",
         "static": "on the value of"},
        {"key": "cap_line", "x": 34.5, "y": 70, "size": 10, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "valid_label", "x": 44.4, "y": 82, "size": 5, "font": "Helvetica", "color": "#6B6480"},
        {"key": "valid_date", "x": 49.2, "y": 91, "size": 8, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "restrictions_label", "x": 5, "y": 106, "size": 5, "font": "Helvetica", "color": "#6B6480"},
        {"key": "restrictions_body", "x": 5, "y": 115, "size": 5, "font": "Helvetica", "color": "#4A4160", "line_height": 9},
        {"key": "barcode_plate", "x": 82.5, "y": 122, "w": 60, "h": 28.66},
        {"key": "barcode", "x": 86.5, "y": 123, "w": 50, "h": 13.4},
        {"key": "voucher_code", "x": 89.8, "y": 148, "size": 7, "font": "Courier", "color": "#231B36"},
    ],
}
