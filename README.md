## SecurePlate - Nigerian License Plate Recognition System

### Updated: Nigerian Plate Format Support

This system now correctly recognizes and validates the **standard Nigerian license plate format**:

**Format: ABC-123DE**

- 3 letters (Local Government Area code, e.g., KJA, ABC, XYZ)
- 3 digits (sequential identifiers, e.g., 123, 456, 999)
- 2 letters (additional unique identifiers, e.g., DE, AB, ZY)

**Total: 8 alphanumeric characters** (hyphen is visual separator only)

### How the System Now Works

1. **Detection**: Captures video and finds rectangular regions that look like license plates
2. **Cropping**: Extracts and perspective-corrects the detected region
3. **OCR**: Reads the text using EasyOCR
4. **Normalization**: Converts OCR output to standard format (removes state names, spaces, hyphens)
5. **Validation**: Checks if extracted text matches Nigerian format (3L-3D-2L)
6. **Database Check**: Looks up plate in allowed_list.csv
7. **Logging**: Records detection in log.csv
8. **Dashboard**: Updates dashboard.html with latest detection
9. **Storage**: Saves plate image to images/ folder

### What Gets Accepted

✅ Valid Nigerian plates:

- ABC123DE (standard)
- ABC-123DE (with hyphen separator)
- ABC 123 DE (with spaces)
- Lagos ABC123DE (state name + plate)
- KADUNA XYZ999AB (another state + plate)
- KJA456ZY (direct valid plate)

### What Gets Rejected

❌ Invalid detections:

- LAGOS (state name only)
- LACOS (misspelled state)
- ABC123 (only 6 chars - missing 2 letters)
- AB123DE (only 7 chars - missing 1 letter)
- ABC12DE (only 7 chars - only 2 digits)
- Random text without proper format

### Running the System

**Standard run (production mode):**

```powershell
python main.py
```

- Runs detection every 2 seconds to avoid duplicates
- Logs valid plates only
- Updates dashboard
- Saves plate crops

**Debug run (recommended for testing):**

```powershell
python test_detection.py
```

- Shows detailed logs every 5 frames
- Displays which contours are evaluated
- Shows raw OCR text → normalized result
- Indicates if format validation passed/failed
- Helps troubleshoot detection issues

### Key Features

✨ **Smart Filtering:**

- Filters contours by aspect ratio (plates are wider than tall)
- Skips tiny regions and state-label-only areas
- Applies perspective correction to un-skew angled plates

✨ **Format Validation:**

- Strict matching to Nigerian plate format
- Rejects partial/malformed plates
- Handles variations (with/without hyphens and spaces)

✨ **State Name Filtering:**

- Recognizes Nigerian state names and filters them out
- Includes common misspellings (e.g., LACOS)
- Extracts actual plate when OCR reads "LAGOS ABC123DE"

✨ **Confidence Scoring:**

- Only logs detections with OCR confidence > 0.5
- Shows confidence level in logs

### File Structure

```
secureplate/
├── main.py                  # Main application
├── test_detection.py        # Debug/test script
├── plate_recognition.py     # OCR & plate detection logic
├── database_check.py        # Database lookups with caching
├── dashboard_update.py      # Dashboard HTML updates
├── allowed_list.csv         # Authorized plates database
├── log.csv                  # Detection log
├── dashboard.html           # Live status dashboard
└── images/                  # Saved plate crops
```

### Troubleshooting

**Still detecting state names?**

- Run with `test_detection.py` to see debug logs
- Check if your plate image is clear and well-lit
- Ensure the actual plate number (3L-3D-2L) is visible

**Not detecting valid plates?**

- Check the debug output to see OCR text
- Verify plate matches Nigerian format exactly
- Try improving lighting or angle

**Dashboard not updating?**

- Check `log.csv` for entries first
- Make sure `dashboard.html` is in working directory
- Verify permissions on `dashboard.html`

### Testing with Your Data

To add custom plates to the allowed list:

```csv
plate,owner,category
ABC123DE,Mr. John,Staff
XYZ999AB,Mrs. Jane,Visitor
KJA456ZY,Dr. Smith,Staff
```

Format:

- `plate`: Must be exactly 8 alphanumeric characters (no hyphens in CSV)
- `owner`: Name of plate owner
- `category`: Staff, Visitor, or custom category

Then run:

```powershell
python main.py
```

The system will recognize "ABC123DE" (with or without hyphens when OCR reads it) and mark it as ALLOWED.
