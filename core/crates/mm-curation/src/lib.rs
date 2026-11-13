//! What stays in the inbox: the classifier, which reads a sender and a subject into a bucket.
//!
//! Pure functions: nothing here reads the disk, the network or the clock. The Python app calls them through
//! `mm-py` while it is still the server, and its tests hold each to the answers it gave.
