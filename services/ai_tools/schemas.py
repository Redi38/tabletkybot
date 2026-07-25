TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_medicines",
            "description": (
                "Get the list of the user's active medicines with their intake schedule, dosage, and remaining course."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_prescriptions",
            "description": (
                "Get the list of the user's active prescriptions — medicine name, "
                "expiration date, how much has already been purchased out of the allowed amount."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_medicine_reminder",
            "description": "Add a new medicine with reminders. duration_days — a realistic course length in days (1-365).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "form": {"type": "string"},
                    "dosage": {"type": "string"},
                    "times": {"type": "array", "items": {"type": "string"}, "description": "Time in HH:MM format"},
                    "duration_days": {"type": "integer", "description": "From 1 to 365 days"},
                    "stock_amount": {"type": "integer"},
                },
                "required": ["name", "form", "dosage", "times", "duration_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_medicine",
            "description": "Change a parameter of an already added medicine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "field": {
                        "type": "string",
                        "enum": ["name", "form", "dosage", "stock_amount", "low_stock_threshold"],
                    },
                    "value": {"type": "string"},
                },
                "required": ["medicine_name", "field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_medicine_removal",
            "description": (
                "Call this when the user wants to archive OR delete a medicine. "
                "This does NOT perform the action immediately — the user will receive a message with buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {"medicine_name": {"type": "string"}},
                "required": ["medicine_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_prescription_entry",
            "description": "Add a new prescription for a medicine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "issued_date": {"type": "string", "description": "Issue date, DD.MM.YY"},
                    "valid_from_date": {"type": "string", "description": "Start date of validity, DD.MM.YY"},
                    "duration_days": {"type": "integer", "enum": [30, 60]},
                    "max_quantity": {"type": "integer"},
                    "reminder_days_before": {"type": "integer"},
                },
                "required": ["medicine_name", "issued_date", "valid_from_date", "duration_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_prescription",
            "description": "Change a parameter of an already added prescription.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "field": {"type": "string", "enum": ["max_quantity", "reminder_days_before", "notes"]},
                    "value": {"type": "string"},
                },
                "required": ["medicine_name", "field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_prescription_bought",
            "description": "Mark the purchase of a certain quantity of units under a prescription.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "amount": {"type": "integer"},
                },
                "required": ["medicine_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_prescription_removal",
            "description": (
                "Call this when the user wants to archive OR delete a prescription. "
                "This does NOT perform the action immediately — the user will receive a message with buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {"medicine_name": {"type": "string"}},
                "required": ["medicine_name"],
            },
        },
    },
]
