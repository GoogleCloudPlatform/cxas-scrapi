# Restaurant Mock Data for CX Transcript Generation

This directory contains mock data representing a restaurant's customer interaction system. It is used for testing the `cxas_scrapi_report_generator` skill.

## Restaurant Details
- **Domain**: Restaurant Customer Support.
- **Core Journeys**:
  - Ordering & Delivery (Placing orders, tracking, cancellation).
  - Reservations & Table Booking.
  - Menu Inquiries (Dietary restrictions, recommendations).
  - Feedback & Complaints.
  - Payments & Rewards.

## File Types
- **Cyara XML Files**: Contain natural end-to-end dialogues for specific test cases.
- **Draw.io Files**: Represent the underlying IVR flow and system states.
- **Python (`app.py`)**: Simulates backend order processing logic.
- **Gradle (`build.gradle`)**: Represents the build configuration for a restaurant management service.

## Extension
The restaurant scenario described in this directory can be extended as needed for future mock data types (e.g., adding mock databases, API specs, etc.).
