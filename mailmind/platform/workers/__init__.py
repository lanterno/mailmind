"""Background and live-IMAP work: the gate every job that talks to an account's mail server
passes (`gates`, over `account_worker`), the workers that wait on a connection (`idle_supervisor`),
the scheduler and the durable queue. Standard library only."""
