"""Native image replacement keeps model state and meaningful storage errors."""
import tempfile
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from learning_paths.keys import LearningPathKey
from learning_paths.models import LearningPath, LearningPathGradingCriteria


class LearningPathImageCleanupTests(TestCase):
    """Exercise LearningPath.save, not only FieldFile.delete in isolation."""

    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.path = LearningPath.objects.create(
            key=LearningPathKey.from_string("path-v1:TEST+IMAGE-CLEANUP+2026+CONTROL"),
            display_name="Image replacement control",
        )
        self.path.image.save("old.png", ContentFile(b"old test image bytes"))
        self.path.refresh_from_db()
        self.old_name = self.path.image.name
        self.storage = self.path.image.storage
        self.path.image.save("new.png", ContentFile(b"new test image bytes"), save=False)
        self.new_name = self.path.image.name

    def assert_new_image_preserved(self):
        self.assertTrue(self.storage.exists(self.new_name))
        self.assertEqual(self.path.image.name, self.new_name)
        persisted = LearningPath.objects.get(pk=self.path.pk)
        self.assertEqual(persisted.image.name, self.new_name)
        self.assertEqual(
            LearningPathGradingCriteria.objects.filter(learning_path=self.path).count(), 1
        )

    def test_replacement_has_no_detached_field_exception(self):
        with self.assertNoLogs("learning_paths.models", level="ERROR"):
            self.path.save()
        self.assertFalse(self.storage.exists(self.old_name))
        self.assert_new_image_preserved()

    def test_real_storage_failure_remains_logged(self):
        with (
            patch.object(self.storage, "delete", side_effect=OSError("storage unavailable")),
            self.assertLogs("learning_paths.models", level="ERROR") as captured,
        ):
            self.path.save()
        self.assertEqual(len(captured.records), 1)
        self.assertIs(captured.records[0].exc_info[0], OSError)
        self.assertIn("Failed to delete old image", captured.records[0].getMessage())
        self.assertTrue(self.storage.exists(self.old_name))
        self.assert_new_image_preserved()
