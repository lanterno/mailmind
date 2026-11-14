//! The outbox as a durable queue its worker drains (`Outbox.worker_loop`): `Workers::start` runs it unless the
//! process only reads, and a send queued, retried or brought forward kicks it. The served suites run each pass
//! themselves (`work outbox`), so there nothing waits on the kick.

use std::sync::Arc;

use mm_compose::delivery::Delivery;
use mm_compose::sending_ports::Worker;
use mm_kernel::errors::Fault;
use mm_mailbox::workers::{Kick, Queue};

/// What wakes the outbox's worker: the kick it waits on.
#[derive(Debug)]
pub(crate) struct KickWorker(pub(crate) Arc<Kick>);

impl Worker for KickWorker {
    fn wake(&self) {
        self.0.kick();
    }
}

/// The outbox, as `mm_mailbox::workers::run_forever` drains it.
#[derive(Debug)]
pub(crate) struct OutboxQueue {
    pub(crate) delivery: Arc<Delivery>,
    pub(crate) kick: Arc<Kick>,
}

impl Queue for OutboxQueue {
    fn tag(&self) -> &'static str {
        "outbox"
    }

    fn kicked(&self) -> Arc<Kick> {
        self.kick.clone()
    }

    /// Sends a crash left going go back to the queue, but one that may have gone out waits for a person.
    fn recover(&self) -> Result<(), Fault> {
        self.delivery.recover()
    }

    fn now(&self) -> i64 {
        self.delivery.now()
    }

    fn until_due(&self, looked: i64) -> i64 {
        self.delivery.until_due(looked)
    }

    fn drain(&self) -> Result<(), Fault> {
        self.delivery.deliver()
    }
}
