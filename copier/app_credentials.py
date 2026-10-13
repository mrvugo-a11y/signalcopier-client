"""Built in settings filled in ONCE by the owner (the seller), never by the customer.

TELEGRAM_API_ID / TELEGRAM_API_HASH:
  1. Log in at https://my.telegram.org with the owner's phone number.
  2. Click "API development tools", create an app (any name, platform Desktop).
  3. Paste "App api_id" (a number) and "App api_hash" (text) below.
  One app ID serves all customers: each customer still logs in with their OWN Telegram account.
  While these are left as 0 / "", the setup wizard asks the customer for their own ID and hash.

LICENCE_URL: the licence check address of your website, e.g. https://yoursite.onrender.com/api/licence
  Leave "" to run the copier in a free 7 day trial mode.

The GitHub build workflow can fill these in from repository secrets
(TG_API_ID, TG_API_HASH, LICENCE_URL), so you do not have to commit them.
"""
TELEGRAM_API_ID = 0
TELEGRAM_API_HASH = ""
LICENCE_URL = ""
