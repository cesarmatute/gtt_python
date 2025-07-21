# Game Sentry 🎮⏱️

Game Sentry is a desktop application designed for parents to manage and monitor their children's computer gaming time. It provides a simple, modern interface for both parents and kids, with features for setting time limits, tracking sessions, and receiving notifications.

## Features

- **👤 Dual Role Interface:** Separate, intuitive views for Parents and Kids.
- **👨‍👩‍👧 User Management:** Add, edit, and delete parent and kid profiles with custom avatars stored in the cloud.
- **⏰ Advanced Time Controls:**
    - Set maximum daily gaming time allowances.
    - Enforce limits on continuous play time per session.
    - Define mandatory break periods after a gaming block.
    - Restrict gaming to specific hours of the day (e.g., 3 PM to 6 PM).
- **📝 Session Logging:** Automatically logs all gaming sessions with start time, stop time, and duration, stored securely in Firebase.
- **✍️ Manual Log Management:** Parents can manually add, edit, or delete session logs to correct inaccuracies.
- **▶️ Real-time Timer:** Kids can easily start and stop their gaming timer directly from their dashboard.
- **🔔 Notifications:**
    - System tray notifications for session start/stop and limit warnings.
    - Real-time email notifications for key events (session start/stop, daily limit reached).
- **🎨 Customization:** Includes user-selectable light and dark themes.
- **📦 Single-Instance Execution:** Prevents multiple copies of the application from running simultaneously.

## Tech Stack

- **Framework:** Python 3
- **GUI:** Tkinter
- **Database:** Google Firebase (Firestore) for user data and session logs.
- **Cloud Storage:** Cloudinary for cloud-based avatar management.
- **Executable Builder:** PyInstaller
- **Windows Integration:** PyWin32 for system tray and single-instance control.

## Setup and Installation

Follow these steps to get a local copy up and running.

### Prerequisites

- Python 3.8+
- Git

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your_username/gtt_python.git
    cd gtt_python
    ```

2.  **Set up credentials:**
    - **Firebase:** Place your Firebase Admin SDK JSON key in the `secrets/` directory. The application expects a file named `game-sentry-qcayd-firebase-adminsdk-fbsvc-da160f8409.json`.
    - **Cloudinary:** The application is pre-configured for a specific Cloudinary instance. To use your own, update the credentials in the `initialize_firebase()` function within `game_sentry.py`.

3.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    venv\Scripts\activate  # On Windows
    # source venv/bin/activate  # On macOS/Linux
    ```

4.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **(Windows Only) Run the `pywin32` post-install script:**
    This step is crucial for `pywin32` to work correctly.
    ```bash
    python venv\Scripts\pywin32_postinstall.py -install
    ```

6.  **Run the application:**
    ```bash
    python game_sentry.py
    ```

## Building the Executable

To create a standalone `.exe` file for distribution on Windows:

1.  Ensure all dependencies, including `pyinstaller`, are installed.
2.  Run the build command from the root of the project directory:

    ```bash
    python -m PyInstaller --onefile --windowed --name GameSentry --add-data "pictures;pictures" --add-data "sounds;sounds" --add-data "secrets;secrets" game_sentry.py
    ```
3.  The final executable, `GameSentry.exe`, will be located in the `dist/` directory.

## License

Distributed under the MIT License. 