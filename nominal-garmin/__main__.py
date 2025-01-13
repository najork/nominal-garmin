import io
import os
import zipfile
from getpass import getpass

import click
import fitdecode
import pandas as pd
import requests
import tabulate
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
)
from garth.exc import GarthHTTPError
from nominal import nominal
from nominal.exceptions import NominalConfigError


def init_garmin() -> Garmin | None:
    tokenstore = "~/.garminconnect"

    try:
        dir_path = os.path.expanduser(tokenstore)
        with open(dir_path, "r") as token_file:
            token = token_file.read()

        garmin = Garmin()
        garmin.login(token)

    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError):
        try:
            print("Please enter your Garmin Connect credentials...")
            email = input("E-mail: ")
            password = getpass("Password: ")

            garmin = Garmin(email=email, password=password)
            garmin.login()

            token = garmin.garth.dumps()
            dir_path = os.path.expanduser(tokenstore)
            with open(dir_path, "w") as token_file:
                token_file.write(token)

        except (
            FileNotFoundError,
            GarthHTTPError,
            GarminConnectAuthenticationError,
            requests.exceptions.HTTPError,
        ) as err:
            print(f"Error: {err}")
            return None

    return garmin


def init_nominal() -> nominal.NominalClient | None:
    try:
        nominal._config.get_token(nominal._DEFAULT_BASE_URL)

    except NominalConfigError:
        print("Please enter your Nominal token...")
        token = getpass("Token: ")
        nominal._config.set_token(nominal._DEFAULT_BASE_URL, token)

    return nominal.get_default_client()


class Clients:
    def __init__(self, garmin: Garmin, nominal: nominal.NominalClient) -> None:
        self.garmin = garmin
        self.nominal = nominal


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.obj = Clients(init_garmin(), init_nominal())


@cli.command("list")
@click.option("--count", default=10, help="Number of activities to list")
@click.pass_context
def list(ctx: click.Context, count: int) -> None:
    table = [["Time", "Name", "Type", "ID"]]
    activities = ctx.obj.garmin.get_activities(0, count)
    for activity in activities:
        table.append(
            [
                activity["startTimeLocal"],
                activity["activityName"],
                activity["activityType"]["typeKey"],
                activity["activityId"],
            ]
        )

    print(tabulate.tabulate(table, headers="firstrow"))


@cli.command("push")
@click.argument("activity_id")
@click.pass_context
def push(ctx: click.Context, activity_id: str) -> None:
    data = ctx.obj.garmin.download_activity(
        activity_id,
        dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
    )

    with zipfile.ZipFile(io.BytesIO(data)) as zip_ref:
        for file_info in zip_ref.infolist():
            with zip_ref.open(file_info) as file:
                df = fit_to_pandas(io.BytesIO(file.read()))
                name = f"{str(ctx.obj.garmin.full_name).replace(' ', '_')}_{str(activity_id)}"
                nominal.upload_pandas(
                    df,
                    name,
                    timestamp_column="timestamp",
                    timestamp_type=nominal.ts.Custom("yyyy-MM-dd HH:mm:ss"),
                )


def fit_to_pandas(bytes_: io.BytesIO) -> pd.DataFrame:
    # Initialize some useful variables for the loops
    check_list = good_list = []
    list_check = {}
    df_activity = pd.DataFrame([])

    # Open the file with fitdecode
    with fitdecode.FitReader(bytes_) as file:
        # Iterate through the .FIT frames
        for frame in file:
            # Procede if the frame object is the correct data type
            if isinstance(frame, fitdecode.records.FitDataMessage):
                # Add the frames and their corresponding counts to a dictionary for debugging
                if frame.name not in check_list:
                    check_list.append(frame.name)
                    list_check[frame.name] = 1
                else:
                    list_check.update({frame.name: list_check.get(frame.name) + 1})

                # If the current frame is a record, we'll reset the row_dict variable
                # and add the field values for all fields in the good_list variable
                if frame.name == "record":
                    row_dict = {}
                    for field in frame.fields:
                        if field.name.find("unknown") < 0:
                            if (
                                field.name not in good_list
                                and field.name.find("unknown") < 0
                            ):
                                good_list.append(field.name)
                            row_dict[field.name] = frame.get_value(field.name)

                    # Append this row's dictionary to the main dataframe
                    df_activity = pd.concat(
                        [df_activity, pd.DataFrame([row_dict])], ignore_index=True
                    )

        # Update the Long/Lat columns to standard degrees
        for column in ["position_lat", "position_long"]:
            df_activity[column] = df_activity[column].apply(
                lambda x: x / ((2**32) / 360)
            )

    return df_activity


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
