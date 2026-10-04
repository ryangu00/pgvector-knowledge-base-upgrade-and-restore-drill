UPDATE jobs SET status = 'cancelled' WHERE status IN ('active', 'running', 'waiting', 'delayed');
