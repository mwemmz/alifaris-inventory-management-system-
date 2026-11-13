# Desktop app

A thin Electron window around the same Django app. It was written but **not built or run** in the environment this was
created in, so treat the first build as a test.

    cd desktop
    npm install
    npm start          # opens the window (and starts Django if start_server is true)
    npm run dist       # builds a Windows installer with electron-builder

Shop PC (one site): leave `start_server: true`, set `project_dir` to the project folder, and install Python + `pip install -r requirements.txt`.
Counter PCs: set `"start_server": false` and `"url": "http://<shop-server>:8000"`.
