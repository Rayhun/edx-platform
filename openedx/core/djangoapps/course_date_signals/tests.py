# lint-amnesty, pylint: disable=missing-module-docstring
from datetime import timedelta
import ddt
from unittest.mock import patch  # lint-amnesty, pylint: disable=wrong-import-order

from cms.djangoapps.contentstore.config.waffle import CUSTOM_PLS
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx.core.djangoapps.course_date_signals.handlers import _gather_graded_items, _get_custom_pacing_children, _has_assignment_blocks, extract_dates_from_course
from openedx.core.djangoapps.course_date_signals.models import SelfPacedRelativeDatesConfig
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from . import utils


@ddt.ddt
class SelfPacedDueDatesTests(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        for i in range(4):
            ItemFactory(parent=self.course, category="sequential", display_name=f"Section {i}")

    def test_basic_spacing(self):
        expected_sections = [
            (0, 'Section 0', timedelta(days=7)),
            (1, 'Section 1', timedelta(days=14)),
            (2, 'Section 2', timedelta(days=21)),
            (3, 'Section 3', timedelta(days=28)),
        ]
        with patch.object(utils, 'get_expected_duration', return_value=timedelta(weeks=4)):
            actual = [(idx, section.display_name, offset) for (idx, section, offset) in utils.spaced_out_sections(self.course)]  # lint-amnesty, pylint: disable=line-too-long

        assert actual == expected_sections

    def test_hidden_sections(self):
        for _ in range(2):
            ItemFactory(parent=self.course, category="sequential", visible_to_staff_only=True)
        expected_sections = [
            (0, 'Section 0', timedelta(days=7)),
            (1, 'Section 1', timedelta(days=14)),
            (2, 'Section 2', timedelta(days=21)),
            (3, 'Section 3', timedelta(days=28)),
        ]
        with patch.object(utils, 'get_expected_duration', return_value=timedelta(weeks=4)):
            actual = [(idx, section.display_name, offset) for (idx, section, offset) in utils.spaced_out_sections(self.course)]  # lint-amnesty, pylint: disable=line-too-long

        assert actual == expected_sections

    def test_dates_for_ungraded_assignments(self):
        """
        _has_assignment_blocks should return true if the argument block
        children leaf nodes include an assignment that is graded and scored
        """
        with modulestore().bulk_operations(self.course.id):
            sequence = ItemFactory(parent=self.course, category="sequential")
            vertical = ItemFactory(parent=sequence, category="vertical")
            sequence = modulestore().get_item(sequence.location)
            assert _has_assignment_blocks(sequence) is False

            # Ungraded problems do not count as assignment blocks
            ItemFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=0,
            )
            sequence = modulestore().get_item(sequence.location)
            assert _has_assignment_blocks(sequence) is False
            ItemFactory.create(
                parent=vertical,
                category='problem',
                graded=False,
                weight=1,
            )
            sequence = modulestore().get_item(sequence.location)
            assert _has_assignment_blocks(sequence) is False

            # Method will return true after adding a graded, scored assignment block
            ItemFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=1,
            )
            sequence = modulestore().get_item(sequence.location)
            assert _has_assignment_blocks(sequence) is True

    def test_sequence_with_graded_and_ungraded_assignments(self):
        """
        _gather_graded_items should set a due date of None on ungraded problem blocks
        even if the block has graded siblings in the sequence
        """
        with modulestore().bulk_operations(self.course.id):
            sequence = ItemFactory(parent=self.course, category="sequential")
            vertical = ItemFactory(parent=sequence, category="vertical")
            ItemFactory.create(
                parent=vertical,
                category='problem',
                graded=False,
                weight=1,
            )
            ungraded_problem_2 = ItemFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=0,
            )
            graded_problem_1 = ItemFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=1,
            )
            expected_graded_items = [
                (ungraded_problem_2.location, {'due': None}),
                (graded_problem_1.location, {'due': 5}),
            ]
            self.assertCountEqual(_gather_graded_items(sequence, 5), expected_graded_items)

    def test_get_custom_pacing_children(self):
        """
        _get_custom_pacing_items should return a list of (block item location, field metadata dictionary)
        where the due dates are set from due_num_weeks
        """
        # A subsection with multiple units but no problems
        with modulestore().bulk_operations(self.course.id):
            sequence = ItemFactory(parent=self.course, category="sequential", due_num_weeks=1)
            vertical1 = ItemFactory(parent=sequence, category='vertical')
            vertical2 = ItemFactory(parent=sequence, category='vertical')
            vertical3 = ItemFactory(parent=sequence, category='vertical')
            expected_dates = [(sequence.location, {'due': timedelta(weeks=1)}), 
                            (vertical1.location, {'due': timedelta(weeks=1)}), 
                            (vertical2.location, {'due': timedelta(weeks=1)}), 
                            (vertical3.location, {'due': timedelta(weeks=1)})]
            self.assertCountEqual(_get_custom_pacing_children(sequence, 1), expected_dates)

        # A subsection with multiple units, each of which has a problem
        with modulestore().bulk_operations(self.course.id):
            sequence = ItemFactory(parent=self.course, category="sequential", due_num_weeks=2)
            vertical1 = ItemFactory(parent=sequence, category='vertical')
            problem1 = ItemFactory(parent=vertical1, category='problem')
            vertical2 = ItemFactory(parent=sequence, category='vertical')
            problem2 = ItemFactory(parent=vertical1, category='problem')
            expected_dates = [(sequence.location, {'due': timedelta(weeks=2)}), 
                            (vertical1.location, {'due': timedelta(weeks=2)}), 
                            (vertical2.location, {'due': timedelta(weeks=2)}), 
                            (problem1.location, {'due': timedelta(weeks=2)}),
                            (problem2.location, {'due': timedelta(weeks=2)})]
            self.assertCountEqual(_get_custom_pacing_children(sequence, 2), expected_dates)

        # A subsection that has ORA as a problem
        with modulestore().bulk_operations(self.course.id):
            sequence = ItemFactory(parent=self.course, category="sequential", due_num_weeks=2)
            vertical1 = ItemFactory(parent=sequence, category='vertical')
            problem1 = ItemFactory(parent=vertical1, category='openassessment')
            expected_dates = [(sequence.location, {'due': timedelta(weeks=2)}), 
                            (vertical1.location, {'due': timedelta(weeks=2)})]
            self.assertCountEqual(_get_custom_pacing_children(sequence, 2), expected_dates)

        # A subsection that has an ORA problem and a non ORA problem
        with modulestore().bulk_operations(self.course.id):
            sequence = ItemFactory(parent=self.course, category="sequential", due_num_weeks=3)
            vertical1 = ItemFactory(parent=sequence, category='vertical')
            problem1 = ItemFactory(parent=vertical1, category='openassessment')
            problem2 = ItemFactory(parent=vertical1, category='problem')
            expected_dates = [(sequence.location, {'due': timedelta(weeks=3)}), 
                            (vertical1.location, {'due': timedelta(weeks=3)}),
                            (problem2.location, {'due': timedelta(weeks=3)})]
            self.assertCountEqual(_get_custom_pacing_children(sequence, 3), expected_dates)

@ddt.ddt
class SelfPacedCustomDueDateTests(SharedModuleStoreTestCase):

    @override_waffle_flag(CUSTOM_PLS, active=True)
    def setUp(self):
        SelfPacedRelativeDatesConfig.objects.create(enabled=True)

        # setUpClassAndTestData() already calls setUpClass on SharedModuleStoreTestCase
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            self.courses = []

            # course 1: with due_num_weeks but without any units
            course1 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course1.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course1.location)
                sequential = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=3)
            course1.child = chapter
            chapter.child = sequential
            self.courses.append(course1)

            # course 2: with due_num_weeks and a unit
            course2 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course2.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course2.location)
                sequential = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=2)
                vertical = ItemFactory.create(category='vertical', parent_location=sequential.location)
            course2.child = chapter
            chapter.child = sequential
            sequential.child = vertical
            self.courses.append(course2)

            # course 3: with due_num_weeks and a problem
            course3 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course3.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course3.location)
                sequential = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=1)
                vertical = ItemFactory.create(category='vertical', parent_location=sequential.location)
                problem = ItemFactory.create(category='problem', parent_location=vertical.location)
            course3.child = chapter
            chapter.child = sequential
            sequential.child = vertical
            vertical.child = problem
            self.courses.append(course3)

            # course 4: with due_num_weeks on all sections
            course4 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course4.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course4.location)
                sequential1 = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=1)
                sequential2 = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=3)
                sequential3 = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=4)
            course4.child = chapter
            chapter.children = [sequential1, sequential2, sequential3]
            self.courses.append(course4)

            # course 5: without due_num_weeks on all sections
            course5 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course5.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course5.location)
                sequential1 = ItemFactory.create(category='sequential', parent_location=chapter.location)
                sequential2 = ItemFactory.create(category='sequential', parent_location=chapter.location)
                sequential3 = ItemFactory.create(category='sequential', parent_location=chapter.location)
            course5.child = chapter
            chapter.children = [sequential1, sequential2, sequential3]
            self.courses.append(course5)

            # course 6: due_num_weeks in one of the sections
            course6 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course6.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course6.location)
                sequential1 = ItemFactory.create(category='sequential', parent_location=chapter.location)
                sequential2 = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=1)
                sequential3 = ItemFactory.create(category='sequential', parent_location=chapter.location)
            course6.child = chapter
            chapter.children = [sequential1, sequential2, sequential3]
            self.courses.append(course6)

            # course 7: a unit with an ORA problem
            course7 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course7.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course7.location)
                sequential = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=1)
                vertical = ItemFactory.create(category='vertical', parent_location=sequential.location)
                problem = ItemFactory.create(category='openassessment', parent_location=vertical.location)
            course7.child = chapter
            chapter.child = sequential
            sequential.child = vertical
            vertical.child = problem
            self.courses.append(course7)

            # course 8: a unit with an ORA problem and a nonORA problem
            course8 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course8.id):
                chapter = ItemFactory.create(category='chapter', parent_location=course8.location)
                sequential = ItemFactory.create(category='sequential', parent_location=chapter.location, due_num_weeks=2)
                vertical = ItemFactory.create(category='vertical', parent_location=sequential.location)
                problem1 = ItemFactory.create(category='openassessment', parent_location=vertical.location)
                problem2 = ItemFactory.create(category='problem', parent_location=vertical.location)
            course8.child = chapter
            chapter.child = sequential
            sequential.child = vertical
            vertical.children = [problem1, problem2]
            self.courses.append(course8)

            # course 9: a section with a subsection that has due_num_weeks and a section without due_num_weeks that has graded content
            course9 = CourseFactory.create(self_paced=True)
            with self.store.bulk_operations(course9.id):
                chapter1 = ItemFactory.create(category='chapter', parent_location=course9.location)
                sequential1 = ItemFactory.create(category='sequential', parent_location=chapter1.location, due_num_weeks=2)
                vertical1 = ItemFactory.create(category='vertical', parent_location=sequential1.location)
                problem1 = ItemFactory.create(category='problem', parent_location=vertical1.location)

                chapter2 = ItemFactory.create(category='chapter', parent_location=course9.location)
                sequential2 = ItemFactory.create(category='sequential', parent_location=chapter2.location, graded=True)
                vertical2 = ItemFactory.create(category='vertical', parent_location=sequential2.location)
                problem2 = ItemFactory.create(category='problem', parent_location=vertical2.location)

            course9.children = [chapter1, chapter2]
            chapter1.child = sequential1
            sequential1.child = vertical1
            vertical1.child = problem1
            chapter2.child = sequential2
            sequential2.child = vertical2
            vertical2.child = problem2
            self.courses.append(course9)


    @override_waffle_flag(CUSTOM_PLS, active=True)
    def test_extract_dates_from_course(self):
        """
        extract_dates_from_course should return a list of (block item location, field metadata dictionary)
        """
        
        # course 1: With due_num_weeks but without any units
        course = self.courses[0]
        chapter = course.child
        sequential = chapter.child
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)), 
                        (sequential.location, {'due': timedelta(days=21)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 2: with due_num_weeks and a unit
        course = self.courses[1]
        chapter = course.child
        sequential = chapter.child
        vertical = sequential.child
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)), 
                        (sequential.location, {'due': timedelta(days=14)}), 
                        (vertical.location, {'due': timedelta(days=14)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 3: with due_num_weeks and a problem
        course = self.courses[2]
        chapter = course.child
        sequential = chapter.child
        vertical = sequential.child
        problem = vertical.child
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)), 
                        (sequential.location, {'due': timedelta(days=7)}), 
                        (vertical.location, {'due': timedelta(days=7)}),
                        (problem.location, {'due': timedelta(days=7)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 4: with due_num_weeks on all sections
        course = self.courses[3]
        chapter = course.child
        sequential = chapter.children
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)),
                        (sequential[0].location, {'due': timedelta(days=7)}),
                        (sequential[1].location, {'due': timedelta(days=21)}),
                        (sequential[2].location, {'due': timedelta(days=28)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 5: without due_num_weeks on all sections
        course = self.courses[4]
        expected_dates = [(course.location, {})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 6: due_num_weeks in one of the sections
        course = self.courses[5]
        chapter = course.child
        sequential = chapter.children
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)),
                        (sequential[1].location, {'due': timedelta(days=7)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 7: a unit with an ORA problem
        course = self.courses[6]
        chapter = course.child
        sequential = chapter.child
        vertical = sequential.child
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)),
                        (sequential.location, {'due': timedelta(days=7)}),
                        (vertical.location, {'due': timedelta(days=7)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 8: a unit with an ORA problem and a nonORA problem
        course = self.courses[7]
        chapter = course.child
        sequential = chapter.child
        vertical = sequential.child
        problem = vertical.children[1]
        expected_dates = [(course.location, {}), 
                        (chapter.location, timedelta(days=28)),
                        (sequential.location, {'due': timedelta(days=14)}),
                        (vertical.location, {'due': timedelta(days=14)}),
                        (problem.location, {'due': timedelta(days=14)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

        # course 9: a section with a subsection that has due_num_weeks and a section without due_num_weeks that has graded content
        course = self.courses[8]
        chapter1 = course.children[0]
        sequential1 = chapter1.child
        vertical1 = sequential1.child
        problem1 = vertical1.child

        chapter2 = course.children[1]
        sequential2 = chapter2.child
        vertical2 = sequential2.child
        problem2 = vertical2.child
        expected_dates = [(course.location, {}), 
                        (chapter1.location, timedelta(days=14)),
                        (sequential1.location, {'due': timedelta(days=14)}),
                        (vertical1.location, {'due': timedelta(days=14)}),
                        (problem1.location, {'due': timedelta(days=14)}),
                        (chapter2.location, timedelta(days=28)),
                        (sequential2.location, {'due': timedelta(days=28)}),
                        (vertical2.location, {'due': timedelta(days=28)}),
                        (problem2.location, {'due': timedelta(days=28)})]
        course = modulestore().get_item(course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)
