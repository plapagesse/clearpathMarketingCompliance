# Mounting the queue view

1. `import QueueView from './QueueView'` in `src/App.tsx` (it pulls in its own `queue.css`).
2. Render `<QueueView />` wherever the queue tab belongs — it is self-contained and takes no props.
3. Backend: `backend/api/queue.py` is already registered in `backend/app.py`; it serves `/api/queue`, `/api/queue/filters`, `/api/queue/submission/<id>`, `.../process`, `.../decision`, and `/api/queue/evidence/<file>`.

Note for the coordinator: this branch shipped standalone because `agent12/input-view` was
not on origin at merge time. When it lands, replace the body of `App.tsx` with the
input view's shell plus a two-button toggle between "Inputs" and "Queue". If the input
view exposes `/api/review/evidence/<file>`, `image_url` in `_item()` (backend/api/queue.py)
can point there instead of the equivalent `/api/queue/evidence/<file>` route.
