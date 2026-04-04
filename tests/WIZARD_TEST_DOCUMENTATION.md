# Custom Course Wizard Test Documentation

## Overview
Comprehensive test suite for the Custom Course Wizard feature, covering all aspects from form validation to end-to-end course creation flows.

## Test Structure

### 1. Validation Tests (`TestPreviewValidation`, `TestCreateValidation`)
Tests input validation at API boundaries to ensure data integrity.

#### Preview Validation Tests
- **test_missing_json_data**: Verifies 400 error when no JSON data provided
- **test_missing_title**: Ensures course title is required
- **test_empty_title**: Validates that whitespace-only titles are rejected
- **test_missing_modules**: Confirms modules array is required
- **test_empty_modules_array**: Checks that at least one module is needed
- **test_too_many_modules**: Enforces maximum of 10 modules
- **test_module_missing_title**: Validates each module has a title
- **test_invalid_depth**: Ensures depth is between 1-5
- **test_negative_depth**: Rejects negative depth values

#### Create Validation Tests
- **test_missing_json_data**: Validates JSON requirement
- **test_missing_required_fields**: Checks all required fields present
- **test_missing_structure**: Ensures structure is provided
- **test_module_count_mismatch**: Validates consistency between modules and structure

**Expected Outcomes:**
- All validation tests should return 400 status codes
- Error messages should be descriptive and actionable
- No data should be written to database on validation failure

### 2. API Interaction Tests (`TestPreviewGeneration`, `TestCourseCreation`)
Tests the core API functionality and LLM interactions.

#### Preview Generation Tests
- **test_successful_preview_generation**: Validates complete preview flow
  - Mocks LLM responses
  - Verifies structure generation
  - Checks response format
  
- **test_llm_failure_fallback**: Tests graceful degradation
  - Simulates LLM failure
  - Verifies fallback structures are used
  - Ensures 200 response with default content
  
- **test_llm_timeout_handling**: Tests timeout scenarios
  - Simulates LLM timeout
  - Verifies error handling
  - Checks for proper error messages

#### Course Creation Tests
- **test_successful_course_creation**: Full creation flow
  - Mocks database operations
  - Verifies hydrator is called
  - Checks course UID generation
  - Validates cleanup (hydrator.close())
  
- **test_hydration_failure_handling**: Tests hydration errors
  - Simulates hydration failure
  - Verifies error response
  - Checks cleanup is performed
  
- **test_database_connection_failure**: Tests DB unavailability
  - Simulates connection failure
  - Verifies 503 status code
  - Checks appropriate error message

**Expected Outcomes:**
- Successful tests return 200 with valid structure
- Failures return appropriate error codes (500, 503)
- Resources are always cleaned up (hydrator.close())
- Database writes are atomic (all or nothing)

### 3. File Upload Tests (`TestFileUploads`)
Tests file upload functionality and validation.

- **test_valid_file_upload**: Tests successful file upload
  - Creates test file
  - Uploads with course data
  - Verifies file is processed
  
- **test_invalid_file_extension**: Tests file type validation
  - Attempts to upload .exe file
  - Should be rejected
  
- **test_large_file_handling**: Tests large file handling
  - Creates 10MB test file
  - Verifies graceful handling

**Expected Outcomes:**
- Valid files (.txt, .md, .pdf, .epub) are accepted
- Invalid extensions are rejected
- Large files are handled without crashes
- Files are saved with secure filenames

### 4. Structure Verification Tests (`TestStructureVerification`)
Tests database structure integrity checks.

- **test_structure_verification_success**: Tests successful verification
  - Mocks verification query
  - Returns expected module count
  - Verifies success response
  
- **test_structure_verification_failure**: Tests verification failure
  - Mocks query returning 0 modules
  - Verifies error response
  - Checks failure handling

**Expected Outcomes:**
- Verification queries are executed after structure creation
- Failures are detected and reported
- Course status is updated appropriately

### 5. Integration Tests (`TestEndToEndFlow`)
Tests complete workflows from start to finish.

- **test_complete_course_creation_flow**: Full preview → create flow
  - Step 1: Generate preview
  - Step 2: Verify preview structure
  - Step 3: Create course with structure
  - Step 4: Verify course creation
  
- **test_preview_then_modify_then_create**: Tests modification workflow
  - Generate initial preview
  - Modify modules
  - Regenerate preview
  - Create course

**Expected Outcomes:**
- Complete flows succeed end-to-end
- Data consistency maintained throughout
- All steps complete successfully
- Final course is valid and accessible

### 6. Error Recovery Tests (`TestErrorRecovery`)
Tests error handling and cleanup mechanisms.

- **test_cleanup_on_creation_failure**: Tests cleanup on failure
  - Simulates creation failure
  - Verifies cleanup attempts
  - Checks course marked as failed
  
- **test_partial_structure_creation**: Tests partial creation handling
  - Simulates failure mid-creation
  - Verifies partial data handling
  - Checks rollback or marking

**Expected Outcomes:**
- Failed courses are marked with status
- Cleanup is always attempted
- No orphaned data in database
- Error messages are logged

### 7. Performance Tests (`TestPerformance`)
Tests timeout handling and performance limits.

- **test_preview_timeout**: Tests preview generation timeout
  - Creates large payload (10 modules, depth 5)
  - Verifies timeout handling
  
- **test_creation_timeout**: Tests creation timeout
  - Creates complex course structure
  - Verifies graceful timeout

**Expected Outcomes:**
- Timeouts return 504 status
- Partial work is handled gracefully
- User receives informative message
- Background processing may continue

### 8. Concurrency Tests (`TestConcurrency`)
Tests concurrent access scenarios.

- **test_multiple_preview_requests**: Tests simultaneous previews
  - Simulates multiple users
  - Verifies no interference
  
- **test_multiple_creation_requests**: Tests concurrent creation
  - Tests database locking
  - Verifies data integrity

**Expected Outcomes:**
- Concurrent requests don't interfere
- Database maintains consistency
- No race conditions
- Proper locking mechanisms work

### 9. UI State Tests (`TestUIState`)
Tests UI state management (requires browser automation).

- **test_wizard_step_navigation**: Tests step transitions
- **test_module_editor_state**: Tests modal state
- **test_structure_preview_rendering**: Tests tree rendering
- **test_progress_modal_updates**: Tests progress updates

**Expected Outcomes:**
- UI state transitions smoothly
- Data persists across steps
- Visual feedback is accurate
- No UI glitches or errors

## Running the Tests

### Prerequisites
```bash
pip install pytest pytest-cov pytest-mock
```

### Run All Tests
```bash
pytest tests/test_custom_course_wizard.py -v
```

### Run Specific Test Class
```bash
pytest tests/test_custom_course_wizard.py::TestPreviewValidation -v
```

### Run with Coverage
```bash
pytest tests/test_custom_course_wizard.py --cov=librarian --cov=services.web_ui.app --cov-report=html
```

### Use Test Runner Script
```bash
chmod +x tests/run_wizard_tests.sh
./tests/run_wizard_tests.sh
```

## Test Data

### Sample Course Data
```python
{
    'title': 'Test Course',
    'description': 'A comprehensive test course',
    'teaching_style': 'Academic',
    'modules': [
        {
            'title': 'Module 1',
            'context': 'Introduction to testing',
            'depth': 3
        }
    ]
}
```

### Sample Structure
```python
{
    'course_uid': 'preview_test123',
    'title': 'Test Course',
    'modules': [
        {
            'title': 'Module 1',
            'units': [
                {
                    'title': 'Unit 1',
                    'lessons': [
                        {
                            'title': 'Lesson 1',
                            'concepts': ['Concept 1', 'Concept 2']
                        }
                    ]
                }
            ]
        }
    ]
}
```

## Mocking Strategy

### Database Mocking
- Mock `read_conn` and `write_conn` from librarian
- Mock execute() to return test data
- Mock has_next() and get_next() for query results

### LLM Mocking
- Mock `llm_generate()` to return test JSON
- Mock `extract_python_list()` to return parsed data
- Test both success and failure scenarios

### File System Mocking
- Mock file operations for upload tests
- Use BytesIO for in-memory file testing
- Mock os.makedirs() and file.save()

## Coverage Goals

- **Line Coverage**: > 90%
- **Branch Coverage**: > 85%
- **Function Coverage**: 100%

### Critical Paths to Cover
1. Validation logic (all branches)
2. Error handling (all exception types)
3. Database operations (success and failure)
4. File uploads (all file types)
5. LLM interactions (success, failure, timeout)
6. Cleanup code (always executed)

## Continuous Integration

### GitHub Actions Workflow
```yaml
name: Wizard Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov pytest-mock
      - name: Run tests
        run: pytest tests/test_custom_course_wizard.py -v --cov
```

## Debugging Failed Tests

### Common Issues

1. **Import Errors**
   - Ensure all dependencies installed
   - Check Python path includes project root
   - Verify mock paths match actual imports

2. **Mock Not Working**
   - Check patch path is correct
   - Verify mock is applied before function call
   - Use `patch.object()` for class methods

3. **Database Errors**
   - Ensure mock connections are set up
   - Check execute() returns proper mock result
   - Verify has_next() and get_next() behavior

4. **Timeout Issues**
   - Increase timeout values in tests
   - Check for infinite loops
   - Verify async operations complete

### Debug Mode
```bash
pytest tests/test_custom_course_wizard.py -v -s --pdb
```

## Test Maintenance

### When to Update Tests

1. **API Changes**: Update request/response formats
2. **Validation Changes**: Update validation test cases
3. **New Features**: Add new test classes
4. **Bug Fixes**: Add regression tests
5. **Performance Changes**: Update timeout values

### Test Review Checklist

- [ ] All new code paths covered
- [ ] Error cases tested
- [ ] Edge cases included
- [ ] Mocks are realistic
- [ ] Tests are independent
- [ ] Tests are deterministic
- [ ] Documentation updated

## Performance Benchmarks

### Expected Test Execution Times

- Validation tests: < 1 second
- API interaction tests: < 5 seconds
- Integration tests: < 10 seconds
- Full suite: < 30 seconds

### Optimization Tips

1. Use fixtures for common setup
2. Mock expensive operations (LLM, DB)
3. Run tests in parallel with pytest-xdist
4. Cache test data when possible
5. Use markers to skip slow tests in development

## Reporting

### Generate HTML Report
```bash
pytest tests/test_custom_course_wizard.py --html=report.html --self-contained-html
```

### Generate JUnit XML
```bash
pytest tests/test_custom_course_wizard.py --junitxml=junit.xml
```

### Coverage Report
```bash
pytest tests/test_custom_course_wizard.py --cov --cov-report=html
open htmlcov/index.html
```

## Future Enhancements

1. **Browser Automation**: Add Selenium/Playwright tests for UI
2. **Load Testing**: Add performance tests with locust
3. **Security Testing**: Add penetration testing
4. **Accessibility Testing**: Add a11y compliance tests
5. **Visual Regression**: Add screenshot comparison tests
