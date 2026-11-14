"""dov tokens are noc: the DOV role is called NOC since 0.5.1 (#703)

Revision ID: a912bb1809d6
Revises: b5b9fde7cf8b
Create Date: 2026-11-14 13:13:30.156959

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a912bb1809d6'
down_revision: Union[str, Sequence[str], None] = 'b5b9fde7cf8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """API tokens made with the old role name get the new one."""
    op.execute("UPDATE api_tokens SET role = 'noc' WHERE role = 'dov'")


def downgrade() -> None:
    op.execute("UPDATE api_tokens SET role = 'dov' WHERE role = 'noc'")
