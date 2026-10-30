"""Notify connected APIs after committed public run changes."""
from alembic import op

revision = "0006_run_notifications"
down_revision = "0005_run_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION public.mic3_notify_run_change() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
    BEGIN
      IF TG_TABLE_NAME = 'runs' THEN
        IF TG_OP = 'UPDATE' THEN
          IF ROW(NEW.model_release_id, NEW.requested_by, NEW.parameters, NEW.status,
                 NEW.created_at, NEW.accepted_at, NEW.dispatch_started_at,
                 NEW.finished_at, NEW.failure_code)
             IS NOT DISTINCT FROM
             ROW(OLD.model_release_id, OLD.requested_by, OLD.parameters, OLD.status,
                 OLD.created_at, OLD.accepted_at, OLD.dispatch_started_at,
                 OLD.finished_at, OLD.failure_code) THEN
            RETURN NULL;
          END IF;
        END IF;
        PERFORM pg_catalog.pg_notify('mic3_run_changes', NEW.id::text);
      ELSIF TG_OP = 'DELETE' THEN
        PERFORM pg_catalog.pg_notify('mic3_run_changes', OLD.run_id::text);
      ELSE
        PERFORM pg_catalog.pg_notify('mic3_run_changes', NEW.run_id::text);
        IF TG_OP = 'UPDATE' AND OLD.run_id IS DISTINCT FROM NEW.run_id THEN
          PERFORM pg_catalog.pg_notify('mic3_run_changes', OLD.run_id::text);
        END IF;
      END IF;
      RETURN NULL;
    END;
    $$;
    CREATE TRIGGER mic3_runs_notify AFTER INSERT OR UPDATE ON public.runs
      FOR EACH ROW EXECUTE FUNCTION public.mic3_notify_run_change();
    CREATE TRIGGER mic3_run_artifacts_notify AFTER INSERT OR UPDATE OR DELETE
      ON public.run_artifacts FOR EACH ROW
      EXECUTE FUNCTION public.mic3_notify_run_change();
    """)


def downgrade():
    op.execute("DROP TRIGGER mic3_run_artifacts_notify ON public.run_artifacts")
    op.execute("DROP TRIGGER mic3_runs_notify ON public.runs")
    op.execute("DROP FUNCTION public.mic3_notify_run_change()")
