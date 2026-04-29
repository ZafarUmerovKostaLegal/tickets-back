ALTER TABLE expense_requests
  ADD COLUMN IF NOT EXISTS payment_deadline DATE NULL;

CREATE INDEX IF NOT EXISTS ix_expense_requests_payment_deadline ON expense_requests (payment_deadline);
