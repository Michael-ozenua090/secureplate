import html as html_lib
from datetime import datetime


def update_dashboard(plate_text, status, info):
    """
    Update the dashboard HTML with the latest plate detection information.

    Reads from dashboard_template.html and writes the populated result to dashboard.html.
    Uses simple token replacement ({{TOKEN}}) instead of regex for robustness.
    All dynamic values are HTML-escaped to prevent malformed output.
    """
    try:
        with open('dashboard_template.html', 'r', encoding='utf-8') as f:
            template = f.read()

        # Determine status text and CSS class
        status_text = "ACCESS GRANTED" if status == "ALLOWED" else "ACCESS DENIED"
        status_class = "allowed" if status == "ALLOWED" else "denied"
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M')

        # Resolve owner/category from info dict
        if info and isinstance(info, dict):
            owner = str(info.get("owner", "Unknown"))
            category = str(info.get("category", "Unknown"))
        else:
            owner = "---"
            category = "---"

        # Replace placeholders; escape all dynamic values to prevent malformed HTML
        output = (template
                  .replace('{{STATUS_CLASS}}', html_lib.escape(status_class))
                  .replace('{{STATUS_TEXT}}',  html_lib.escape(status_text))
                  .replace('{{PLATE}}',        html_lib.escape(str(plate_text)))
                  .replace('{{OWNER}}',        html_lib.escape(owner))
                  .replace('{{CATEGORY}}',     html_lib.escape(category))
                  .replace('{{TIME}}',         html_lib.escape(current_time)))

        with open('dashboard.html', 'w', encoding='utf-8') as f:
            f.write(output)

    except FileNotFoundError:
        print("Error updating dashboard: dashboard_template.html not found")
    except Exception as e:
        print(f"Error updating dashboard: {str(e)}")