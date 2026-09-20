use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunKind {
    Generation,
    Batch,
}

impl std::fmt::Display for RunKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunKind::Generation => write!(f, "generation"),
            RunKind::Batch => write!(f, "batch"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ActiveClaim {
    pub kind: RunKind,
    pub request_id: String,
}

#[derive(Debug, Default)]
pub struct RunClaimCoordinator {
    active: Option<ActiveClaim>,
    cleanup_incomplete: Option<ActiveClaim>,
}

pub type SharedRunClaim = std::sync::Mutex<RunClaimCoordinator>;

impl RunClaimCoordinator {
    pub const fn new() -> Self {
        Self {
            active: None,
            cleanup_incomplete: None,
        }
    }

    /// Attempts to claim the single execution slot for a run.
    /// Returns an error if another run is active or prior cleanup is incomplete.
    pub fn claim(&mut self, kind: RunKind, request_id: &str) -> Result<(), &'static str> {
        if self.cleanup_incomplete.is_some() {
            return Err("RUN_ACTIVE: Previous run cleanup is incomplete.");
        }
        if self.active.is_some() {
            return Err("RUN_ACTIVE: Another operation is currently in progress.");
        }
        self.active = Some(ActiveClaim {
            kind,
            request_id: request_id.to_string(),
        });
        Ok(())
    }

    /// Releases a claim for the given kind and request_id.
    /// If cleanup_incomplete is true, records the incomplete cleanup state
    /// which continues to block new runs.
    pub fn release(&mut self, kind: RunKind, request_id: &str, cleanup_incomplete: bool) -> bool {
        if let Some(current) = &self.active {
            if current.kind == kind && current.request_id == request_id {
                self.active = None;
                if cleanup_incomplete {
                    self.cleanup_incomplete = Some(ActiveClaim {
                        kind,
                        request_id: request_id.to_string(),
                    });
                }
                return true;
            }
        }
        false
    }

    /// Clears an incomplete cleanup state once verified settled.
    pub fn resolve_incomplete_cleanup(&mut self, kind: RunKind, request_id: &str) -> bool {
        if let Some(inc) = &self.cleanup_incomplete {
            if inc.kind == kind && inc.request_id == request_id {
                self.cleanup_incomplete = None;
                return true;
            }
        }
        false
    }

    pub fn get_active(&self) -> Option<ActiveClaim> {
        self.active.clone()
    }

    pub fn get_close_owner(&self) -> Option<ActiveClaim> {
        self.active
            .clone()
            .or_else(|| self.cleanup_incomplete.clone())
    }

    pub fn is_active(&self) -> bool {
        self.active.is_some() || self.cleanup_incomplete.is_some()
    }

    pub fn has_incomplete_cleanup(&self) -> bool {
        self.cleanup_incomplete.is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_claim_and_release() {
        let mut coord = RunClaimCoordinator::new();
        assert!(!coord.is_active());
        assert_eq!(coord.get_active(), None);
        assert_eq!(coord.get_close_owner(), None);

        assert!(coord.claim(RunKind::Generation, "gen-001").is_ok());
        assert!(coord.is_active());
        assert_eq!(
            coord.get_active(),
            Some(ActiveClaim {
                kind: RunKind::Generation,
                request_id: "gen-001".to_string(),
            })
        );
        assert_eq!(
            coord.get_close_owner(),
            Some(ActiveClaim {
                kind: RunKind::Generation,
                request_id: "gen-001".to_string(),
            })
        );

        assert!(coord.release(RunKind::Generation, "gen-001", false));
        assert!(!coord.is_active());
        assert_eq!(coord.get_active(), None);
    }

    #[test]
    fn test_concurrent_claim_rejected() {
        let mut coord = RunClaimCoordinator::new();
        assert!(coord.claim(RunKind::Generation, "gen-001").is_ok());

        // Same kind rejection
        let err1 = coord.claim(RunKind::Generation, "gen-002");
        assert!(err1.is_err());

        // Cross-kind rejection: Generation blocks Batch
        let err2 = coord.claim(RunKind::Batch, "batch-001");
        assert!(err2.is_err());

        assert!(coord.release(RunKind::Generation, "gen-001", false));

        // Now Batch can claim
        assert!(coord.claim(RunKind::Batch, "batch-001").is_ok());

        // Batch blocks Generation
        let err3 = coord.claim(RunKind::Generation, "gen-003");
        assert!(err3.is_err());

        assert!(coord.release(RunKind::Batch, "batch-001", false));
        assert!(!coord.is_active());
    }

    #[test]
    fn test_cleanup_incomplete_blocks_next_run() {
        let mut coord = RunClaimCoordinator::new();
        assert!(coord.claim(RunKind::Batch, "batch-fail").is_ok());

        // Release with cleanup_incomplete = true
        assert!(coord.release(RunKind::Batch, "batch-fail", true));

        // Not currently active, but cleanup is incomplete
        assert!(coord.has_incomplete_cleanup());
        assert!(coord.is_active());
        assert_eq!(coord.get_active(), None);
        assert_eq!(
            coord.get_close_owner(),
            Some(ActiveClaim {
                kind: RunKind::Batch,
                request_id: "batch-fail".to_string(),
            })
        );

        // Subsequent claims must be rejected
        assert!(coord.claim(RunKind::Generation, "gen-new").is_err());
        assert!(coord.claim(RunKind::Batch, "batch-new").is_err());

        // Resolving incomplete cleanup clears the block
        assert!(coord.resolve_incomplete_cleanup(RunKind::Batch, "batch-fail"));
        assert!(!coord.is_active());
        assert!(!coord.has_incomplete_cleanup());

        // Now claim succeeds
        assert!(coord.claim(RunKind::Generation, "gen-new").is_ok());
    }

    #[test]
    fn test_mismatched_release_ignored() {
        let mut coord = RunClaimCoordinator::new();
        assert!(coord.claim(RunKind::Generation, "gen-001").is_ok());

        // Wrong kind
        assert!(!coord.release(RunKind::Batch, "gen-001", false));
        // Wrong ID
        assert!(!coord.release(RunKind::Generation, "gen-wrong", false));

        // Original claim is still active
        assert!(coord.is_active());
        assert!(coord.release(RunKind::Generation, "gen-001", false));
        assert!(!coord.is_active());
    }
}
