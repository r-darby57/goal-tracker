# Goal Tracker

A web application for tracking and managing personal goals with integration to Google Calendar and Strava.

## Features

- Create and manage goals with progress tracking
- Check-in system for daily goal updates
- Integration with Google Calendar for event tracking
- Strava API integration for fitness activities
- Dashboard for goal overview
- Database-backed storage

## Tech Stack

- **Backend**: Python Flask
- **Database**: SQLite
- **Frontend**: HTML/CSS/JavaScript
- **Integrations**: Google Calendar API, Strava API

## Installation

1. Clone the repository:
```bash
git clone git@github.com:r-darby57/goal-tracker.git
cd goal-tracker
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python app.py
```

The app will be available at `http://localhost:5000`

## Configuration

Create a `.env` file with your API credentials:
- Google Calendar API credentials
- Strava API credentials

## License

MIT License - see LICENSE file for details
