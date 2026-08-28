# Running the real application on Vercel

These two files put the working backend back — the reader, the upload, the
review queue, all of it.

They live here rather than in `api/` because Vercel turns any `.py` file in a
top-level `api/` folder into a serverless function, and the current
deployment is deliberately static: a folder of files with nothing running
behind it, so that nothing can crash.

`.vercelignore` does not prevent this. It applies only to `vercel` CLI
uploads; a deployment triggered by a Git push clones the whole repository and
ignores it entirely, which is exactly how a function nobody wanted kept
appearing.

To restore the backend:

    mkdir api
    git mv deploy/vercel-function/index.py api/index.py
    git mv deploy/vercel-function/requirements.txt api/requirements.txt

then replace `vercel.json` with the function version from commit 3948954, and
attach a Postgres database in the Vercel dashboard under Storage.
