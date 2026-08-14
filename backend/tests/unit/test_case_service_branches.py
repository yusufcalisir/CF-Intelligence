"""Targeted Branch Coverage Tests for Case Management Service."""

import pytest
from app.application.services.case_service import CaseManagementService
from app.domain.enums import CasePriority, CaseStatus


class TestCaseServiceBranches:
    """Test all transition branches, four-eyes validation, and hash-chaining in CaseManagementService."""

    @pytest.fixture(autouse=True)
    def setup_service(self):
        self.service = CaseManagementService()

    def test_case_lifecycle_and_four_eyes_branches(self):
        # 1. Create Case
        case = self.service.create_case(
            title="Suspicious Structuring Pattern",
            priority=CasePriority.P1_CRITICAL,
            alert_ids=["ALT-001", "ALT-002"],
        )
        assert case.status == CaseStatus.OPEN
        assert len(case.timeline) == 1
        first_event = case.timeline[0]
        assert first_event.metadata.get("parent_hash") == "0" * 64
        assert "hash" in first_event.metadata

        # 2. Assign Case
        assigned_case = self.service.assign_case(case.id, investigator="analyst_alice")
        assert assigned_case.status == CaseStatus.ASSIGNED
        assert assigned_case.assigned_to == "analyst_alice"
        assert len(assigned_case.timeline) == 2
        assert assigned_case.timeline[1].metadata.get("parent_hash") == first_event.metadata.get("hash")

        # 3. Add Note
        note = self.service.add_note(case.id, author="analyst_alice", content="Reviewing wire sequences")
        assert note.content == "Reviewing wire sequences"
        assert len(self.service.get_case(case.id).notes) == 1

        # 4. Valid Transition: ASSIGNED -> INVESTIGATING
        investigating_case = self.service.change_status(
            case.id,
            new_status=CaseStatus.INVESTIGATING,
            actor="analyst_alice",
        )
        assert investigating_case.status == CaseStatus.INVESTIGATING

        # 5. Invalid Transition: INVESTIGATING -> OPEN (not allowed)
        with pytest.raises(ValueError, match="Invalid transition"):
            self.service.change_status(
                case.id,
                new_status=CaseStatus.OPEN,
                actor="analyst_alice",
            )

        # 6. Four-Eyes Principle Failure: Missing supervisor signature
        with pytest.raises(ValueError, match="requires secondary supervisor signature"):
            self.service.change_status(
                case.id,
                new_status=CaseStatus.CLOSED_CONFIRMED,
                actor="analyst_alice",
                supervisor_signature=None,
            )

        # 7. Four-Eyes Principle Failure: Same supervisor signature as actor
        with pytest.raises(ValueError, match="must be different from the analyst actor"):
            self.service.change_status(
                case.id,
                new_status=CaseStatus.CLOSED_CONFIRMED,
                actor="analyst_alice",
                supervisor_signature="analyst_alice",
            )

        # 8. Four-Eyes Principle Success: Valid closure with distinct supervisor
        closed_case = self.service.change_status(
            case.id,
            new_status=CaseStatus.CLOSED_CONFIRMED,
            actor="analyst_alice",
            supervisor_signature="supervisor_bob",
        )
        assert closed_case.status == CaseStatus.CLOSED_CONFIRMED
        assert closed_case.closed_at is not None

        # 9. Link alert to existing case
        self.service.link_alert(case.id, "ALT-003")
        updated_case = self.service.get_case(case.id)
        assert "ALT-003" in updated_case.alert_ids

        # 10. Export markdown summary
        summary = self.service.export_summary(case.id)
        assert "Suspicious Structuring Pattern" in summary
        assert "Timeline" in summary
