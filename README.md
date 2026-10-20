# AquaFlow Backend (Python / Flask)

The Flask app serves the website (static/index.html), the dashboards, and the accounts, quotes and orders.

## Setup
    python -m venv venv
    source venv/bin/activate        # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env            # set SECRET_KEY and ADMIN_TOKEN
    python app.py

Open http://localhost:5000 (not by double-clicking the HTML file).

## Test accounts (change before going live)
| Role   | User ID          | Password |
|--------|------------------|----------|
| Staff  | staff1 to staff4 | 1234     |
| Driver | driver1 to driver8 | 1234 |

Clients sign up on the website. Their user ID is their email.

## Project layout
    app.py              Flask app and API
    static/index.html   AquaFlow website
    static/*-dashboard.html   Client, staff and driver dashboards (placeholders for now)
    requirements.txt
    .env.example

## Endpoints
- GET  /api/csrf      security token (the website requests this before each form submit)
- POST /api/login     {username, password}
- POST /api/register  {name, phone, email, password}
- POST /api/quote     quote request
- POST /api/orders    order (items are priced on the server)
- GET  /api/quotes    admin, needs Authorization: Bearer <ADMIN_TOKEN>
- GET  /api/orders    admin, needs Authorization: Bearer <ADMIN_TOKEN>
- GET  /api/health    health check

## Note
If you ran an earlier version, delete aquaflow.db once so the new tables and test accounts are created.
