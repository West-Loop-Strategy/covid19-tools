import os
import json

import requests

from etl.idph import IDPH
from utils.format_helper import (
    idph_get_date,
)
from utils.metadata_helper import MetadataHelper

CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))

ROOT_URL = "https://idph.illinois.gov/DPHPublicInformation/api/covidVaccine/getVaccineAdministrationCurrent"
COUNTY_COVID_LINK_FORMAT = "https://idph.illinois.gov/DPHPublicInformation/api/covidVaccine/getVaccineAdministration?countyName={}"
COUNTY_DEMO_LINK_FORMAT = "https://idph.illinois.gov/DPHPublicInformation/api/covidvaccine/getVaccineAdministrationDemos?countyname={}"
TOTAL_VACCINE_LINK = "https://idph.illinois.gov/DPHPublicInformation/api/covidvaccine/getStatewideVaccineDetails"


class IDPH_VACCINE(IDPH):
    def __init__(self, base_url, access_token, s3_bucket):
        super().__init__(base_url, access_token, s3_bucket)

        self.program_name = "open"
        self.project_code = "IDPH-Vaccine"
        self.metadata_helper = MetadataHelper(
            base_url=self.base_url,
            program_name=self.program_name,
            project_code=self.project_code,
            access_token=access_token,
        )
        self.country = "US"
        self.state = "IL"
        self.date = ""
        self.counties_inventory = {}

        self.summary_locations = {}
        self.summary_clinicals = {}

    def parse_list_of_counties(self):
        response = requests.get(ROOT_URL, headers={"content-type": "json"})
        json_response = json.loads(response.text)
        self.date = idph_get_date(json_response.get("lastUpdatedDate"))
        root_json = json_response.get("VaccineAdministration")
        counties = []
        if root_json is None:
            return counties
        for item in root_json:
            county = item.get("CountyName")
            counties.append(county)
            self.counties_inventory[county] = item
        return counties

    def files_to_submissions(self):
        """
        Reads JSON file and convert the data to Sheepdog records
        """
        counties = self.parse_list_of_counties()
        print(counties)

        # latest_submitted_date = self.metadata_helper.get_str_latest_submitted_date_idph()
        # if latest_submitted_date == self.date:
        #     print("Nothing to submit: data of latest submitted date and IDPH are the same.")
        #     return
        #
        # print(f"Getting data for date: {self.date}")
        self.parse_file()

    def parse_file(self):
        """
        Converts a JSON files to data we can submit via Sheepdog. Stores the
        records to submit in `self.summary_locations` and `self.summary_clinicals`.

        Args:
            latest_submitted_date (date): the date of latest available "summary_clinical" for project
            url (str): URL at which the JSON file is available
        """
        state_summary_clinical_submitter_id = self.parse_county_data()
        self.parse_total_state_wide(state_summary_clinical_submitter_id)

    def parse_county_data(self):
        county_vaccine_mapping = {
            "CountyName": "county",
            "AdministeredCount": "vaccine_administered_count",
            "AdministeredCountChange": "vaccine_administered_count_change",
            "AdministeredCountRollAvg": "vaccine_administered_count_roll_avg",
            "PersonsFullyVaccinated": "vaccine_persons_fully_vaccinated",
            "Report_Date": "date",
            "PctVaccinatedPopulation": "vaccine_persons_fully_vaccinated_pct",
        }

        county_demo_mapping = {
            "CountyName": "county",
            "AgeGroup": "age_group",
            "Race": "race",
            "AdministeredCount": "vaccine_administered_count",
            "PersonsFullyVaccinated": "vaccine_persons_fully_vaccinated",
            "Report_Date": "date",
        }

        inventory_reported = {
            "LHDReportedInventory": "vaccine_LHDR_reported_inventory",
            "CommunityReportedInventory": "vaccine_community_reported_inventory",
            "TotalReportedInventory": "vaccine_reported_inventory",
            "InventoryReportDate": "date",
        }

        counties = self.parse_list_of_counties()
        state_summary_clinical_submitter_id = ""
        for county in counties:
            county_covid_response = requests.get(
                COUNTY_COVID_LINK_FORMAT.format(county),
                headers={"content-type": "json"},
            )
            county_covid_data = json.loads(county_covid_response.text).get(
                "CurrentVaccineAdministration"
            )
            # county_demo_response = requests.get(COUNTY_DEMO_LINK_FORMAT.format(county),
            #                                 headers={"content-type": "json"})
            # county_demo_data = json.loads(county_demo_response.text)

            (
                summary_location_submitter_id,
                summary_clinical_submitter_id,
            ) = self.get_location_and_clinical_submitter_id(county, self.date)
            if county.lower() == "illinois":
                state_summary_clinical_submitter_id = summary_clinical_submitter_id

            summary_location = {
                "country_region": self.country,
                "submitter_id": summary_location_submitter_id,
                "projects": [{"code": self.project_code}],
                "province_state": self.state,
            }
            summary_clinical = {
                "submitter_id": summary_clinical_submitter_id,
                "date": self.date,
                "summary_locations": [{"submitter_id": summary_location_submitter_id}],
            }
            for (key, value) in county_vaccine_mapping.items():
                summary_clinical[value] = county_covid_data.get(key)
            # for (key, value) in county_demo_mapping.items():
            #     summary_clinical[value] = county_demo_data.get(key)
            for (key, value) in inventory_reported.items():
                if county in self.counties_inventory:
                    summary_clinical[value] = self.counties_inventory[county].get(key)

            self.summary_locations[summary_location_submitter_id] = summary_location
            self.summary_clinicals[summary_clinical_submitter_id] = summary_clinical
        return state_summary_clinical_submitter_id

    def parse_total_state_wide(self, state_summary_clinical_submitter_id):
        state_total_data = requests.get(
            TOTAL_VACCINE_LINK, headers={"content-type": "json"}
        )
        total_vaccine_mapping = {
            "Total_Delivered": "vaccine_total_delivered_vaccine_doses",
            "Total_Administered": "vaccine_IL_total_administered_vaccine_doses",
            "Persons_Fully_Vaccinated": "vaccine_IL_total_persons_fully_vaccinated",
            "LTC_Allocated": "vaccine_long_term_care_allocated",
            "LTC_Administered": "vaccine_long_term_care_administered",
            "Report_Date": "date",
        }
        for (key, value) in total_vaccine_mapping.items():
            self.summary_clinicals[state_summary_clinical_submitter_id][
                value
            ] = state_total_data.get(key)

    def submit_metadata(self):
        print("Submitting data...")
        print("Submitting summary_location data")
        for sl in self.summary_locations.values():
            sl_record = {"type": "summary_location"}
            sl_record.update(sl)
            self.metadata_helper.add_record_to_submit(sl_record)
        self.metadata_helper.batch_submit_records()

        print("Submitting summary_clinical data")
        for sc in self.summary_clinicals.values():
            sc_record = {"type": "summary_clinical"}
            sc_record.update(sc)
            self.metadata_helper.add_record_to_submit(sc_record)
        self.metadata_helper.batch_submit_records()
